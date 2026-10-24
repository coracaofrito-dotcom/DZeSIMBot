
import os
import logging
import uuid

import requests
import mercadopago

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from saldo import (
    consultar_saldo,
    registrar_cobranca,
    consultar_cobranca,
    confirmar_pagamento,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN")
MP_PAYER_EMAIL = os.getenv("MP_PAYER_EMAIL")

VALOR_MINIMO_PIX = 5.00
URL_PRODUTOS = "https://5sim.net/v1/guest/products/"
URL_PAISES = "https://5sim.net/v1/guest/countries"
URL_CAMBIO = "https://api.frankfurter.dev/v1/latest"

LUCRO_WHATSAPP = 10.00
LUCRO_TELEGRAM = 3.00
LUCRO_SMS = 2.00

SERVICOS = {
    "whatsapp": ("WhatsApp", LUCRO_WHATSAPP),
    "telegram": ("Telegram", LUCRO_TELEGRAM),
    "sms": ("SMS", LUCRO_SMS),
}

ITENS_POR_PAGINA = 8


def dinheiro(valor):
    return f"R$ {valor:.2f}".replace(".", ",")


def obter_cambio():
    try:
        resposta = requests.get(
            URL_CAMBIO,
            params={"from": "USD", "to": "BRL"},
            timeout=15,
        )
        resposta.raise_for_status()
        return float(resposta.json()["rates"]["BRL"])
    except (requests.RequestException, ValueError, KeyError, TypeError):
        logger.exception("Não foi possível obter a cotação.")
        return None


def buscar_produtos(pais="brazil", operadora="any"):
    try:
        resposta = requests.get(
            f"{URL_PRODUTOS}{pais}/{operadora}",
            timeout=20,
        )
        resposta.raise_for_status()
        dados = resposta.json()
        return dados if isinstance(dados, dict) else {}
    except (requests.RequestException, ValueError):
        logger.exception("Erro ao consultar catálogo 5SIM.")
        return {}


def encontrar_servicos(produtos):
    encontrados = []

    for nome, operadoras in produtos.items():
        if not isinstance(operadoras, dict):
            continue

        for operadora, detalhes in operadoras.items():
            if not isinstance(detalhes, dict):
                continue

            try:
                preco = float(detalhes.get("Price", 0) or 0)
                quantidade = float(detalhes.get("Qty", 0) or 0)
            except (TypeError, ValueError):
                continue

            if preco > 0 and quantidade > 0:
                encontrados.append((str(nome), str(operadora), preco, quantidade))

    encontrados.sort(key=lambda item: (item[0].lower(), item[1].lower()))
    return encontrados


def montar_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "📱 Catálogo do Brasil (preços)",
            callback_data="servicos:brazil:0",
        )],
        [InlineKeyboardButton(
            "🌎 Países disponíveis",
            callback_data="paises:0",
        )],
        [InlineKeyboardButton(
            "💰 Meu saldo",
            callback_data="meu_saldo",
        )],
        [InlineKeyboardButton(
            "💳 Recarregar saldo via PIX",
            callback_data="recarregar_pix",
        )],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "🇧🇷 *DZ E-SIM*\n\n"
        "Consulte preços e disponibilidade informativa.\n"
        "Compras e entrega de números ainda não estão habilitadas.",
        reply_markup=montar_menu(),
        parse_mode="Markdown",
    )


async def mostrar_paises(query, pagina=0):
    try:
        resposta = requests.get(URL_PAISES, timeout=20)
        resposta.raise_for_status()
        dados = resposta.json()
    except (requests.RequestException, ValueError):
        await query.edit_message_text(
            "Não consegui consultar os países agora. Tente novamente.",
            reply_markup=montar_menu(),
        )
        return

    if not isinstance(dados, dict) or not dados:
        await query.edit_message_text(
            "Nenhum país foi retornado pelo fornecedor.",
            reply_markup=montar_menu(),
        )
        return

    paises = sorted(dados.items(), key=lambda item: item[0].lower())
    inicio = pagina * ITENS_POR_PAGINA
    lista = paises[inicio:inicio + ITENS_POR_PAGINA]

    botoes = []
    for codigo, detalhes in lista:
        nome = codigo
        if isinstance(detalhes, dict):
            nome = detalhes.get("name") or codigo

        botoes.append([
            InlineKeyboardButton(
                str(nome)[:50],
                callback_data=f"servicos:{codigo}:{0}",
            )
        ])

    navegacao = []
    if pagina > 0:
        navegacao.append(
            InlineKeyboardButton("⬅️ Anterior", callback_data=f"paises:{pagina - 1}")
        )
    if inicio + ITENS_POR_PAGINA < len(paises):
        navegacao.append(
            InlineKeyboardButton("Próxima ➡️", callback_data=f"paises:{pagina + 1}")
        )
    if navegacao:
        botoes.append(navegacao)

    botoes.append([InlineKeyboardButton("🏠 Menu", callback_data="inicio")])

    await query.edit_message_text(
        f"🌎 *Países disponíveis no fornecedor*\n"
        f"Página {pagina + 1} de {max(1, (len(paises) - 1) // ITENS_POR_PAGINA + 1)}",
        reply_markup=InlineKeyboardMarkup(botoes),
        parse_mode="Markdown",
    )


async def mostrar_catalogo(query, pais, pagina=0):
    produtos = buscar_produtos(pais, "any")
    encontrados = encontrar_servicos(produtos)

    if not encontrados:
        await query.edit_message_text(
            "Não encontrei serviços com estoque disponível para esse país.\n"
            "Tente novamente mais tarde.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Menu", callback_data="inicio")
            ]]),
        )
        return

    cambio = obter_cambio()
    inicio = pagina * ITENS_POR_PAGINA
    lista = encontrados[inicio:inicio + ITENS_POR_PAGINA]

    botoes = []
    linhas = [
        f"📱 *Catálogo informativo — {pais.upper()}*",
        "Preços e estoque consultados no fornecedor.",
        "",
    ]

    for indice, (nome, operadora, preco_usd, quantidade) in enumerate(
        lista, start=inicio + 1
    ):
        nome_servico = SERVICOS.get(nome.lower(), (nome, 0))[0]
        margem = SERVICOS.get(nome.lower(), (nome, 0))[1]

        if cambio is None:
            preco_texto = "Preço temporariamente indisponível"
        else:
            preco_texto = dinheiro(preco_usd * cambio + margem)

        linhas.append(
            f"{indice}. {nome_servico} — {operadora}\n"
            f"   Preço estimado: {preco_texto}\n"
            f"   Estoque informado: {quantidade:g}"
        )

        botoes.append([
            InlineKeyboardButton(
                f"ℹ️ {indice}. {nome_servico} — {operadora}"[:60],
                callback_data=f"detalhe:{pais}:{nome}",
            )
        ])

    navegacao = []
    if pagina > 0:
        navegacao.append(
            InlineKeyboardButton(
                "⬅️ Anterior",
                callback_data=f"servicos:{pais}:{pagina - 1}",
            )
        )
    if inicio + ITENS_POR_PAGINA < len(encontrados):
        navegacao.append(
            InlineKeyboardButton(
                "Próxima ➡️",
                callback_data=f"servicos:{pais}:{pagina + 1}",
            )
        )
    if navegacao:
        botoes.append(navegacao)

    botoes.append([InlineKeyboardButton("🏠 Menu", callback_data="inicio")])

    linhas.extend([
        "",
        "ℹ️ Consulta informativa. Compra e entrega ainda não estão habilitadas.",
    ])

    await query.edit_message_text(
        "\n".join(linhas),
        reply_markup=InlineKeyboardMarkup(botoes),
        parse_mode="Markdown",
    )


async def criar_cobranca_pix(valor, telegram_id):
    if not MP_ACCESS_TOKEN:
        return None, "O PIX ainda não está configurado no servidor."

    if not MP_PAYER_EMAIL:
        return None, (
            "Falta configurar MP_PAYER_EMAIL com um e-mail válido "
            "do pagador no ambiente do bot."
        )

    if valor < VALOR_MINIMO_PIX:
        return None, f"A recarga mínima é {dinheiro(VALOR_MINIMO_PIX)}."

    referencia = f"dz-esim-{telegram_id}-{uuid.uuid4().hex}"
    sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

    pagamento = {
        "transaction_amount": round(float(valor), 2),
        "description": "Recarga de saldo DZ E-SIM",
        "payment_method_id": "pix",
        "payer": {"email": MP_PAYER_EMAIL},
        "external_reference": referencia,
    }

    try:
        resultado = sdk.payment().create(pagamento)
        resposta = resultado.get("response", {})
        status_http = resultado.get("status")

        if status_http not in (200, 201) or not resposta.get("id"):
            logger.error("Mercado Pago recusou a criação da cobrança.")
            return None, "Não foi possível criar o PIX. Tente novamente mais tarde."

        pagamento_id = str(resposta["id"])
        dados_pix = resposta.get("point_of_interaction", {}).get(
            "transaction_data", {}
        )
        codigo_pix = dados_pix.get("qr_code")

        if not codigo_pix:
            return None, "O Mercado Pago não retornou o código PIX."

        gravado = registrar_cobranca(
            telegram_id,
            pagamento_id,
            valor,
            referencia,
        )
        if not gravado:
            logger.error("Não foi possível registrar a cobrança no banco.")
            return None, "Não foi possível registrar a cobrança com segurança."

        return {
            "id": pagamento_id,
            "codigo": codigo_pix,
            "valor": round(float(valor), 2),
        }, None

    except Exception:
        logger.exception("Erro ao criar cobrança PIX.")
        return None, "Erro ao criar o PIX. Tente novamente mais tarde."


async def verificar_pagamento(pagamento_id, telegram_id):
    cobranca = consultar_cobranca(pagamento_id)

    if not cobranca or cobranca["telegram_id"] != int(telegram_id):
        return False, "Não encontrei essa cobrança na sua conta."

    if cobranca["status"] == "paga":
        return True, "Esse pagamento já foi confirmado."

    if not MP_ACCESS_TOKEN:
        return False, "O Mercado Pago não está configurado no servidor."

    try:
        sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
        resultado = sdk.payment().get(str(pagamento_id))
        resposta = resultado.get("response", {})

        if resultado.get("status") != 200:
            return False, "Não consegui consultar o pagamento agora."

        if resposta.get("status") != "approved":
            estado = resposta.get("status", "desconhecido")
            return False, f"Pagamento ainda não aprovado. Status: {estado}."

        referencia = resposta.get("external_reference", "")
        valor = resposta.get("transaction_amount")

        if valor is None:
            return False, "Não foi possível validar o valor do pagamento."

        confirmado = confirmar_pagamento(
            pagamento_id,
            float(valor),
            referencia,
        )

        if not confirmado:
            logger.warning(
                "Pagamento não creditado: validação local falhou para ID %s",
                pagamento_id,
            )
            return False, (
                "O pagamento foi consultado, mas não passou na validação "
                "da cobrança registrada. Entre em contato com o suporte."
            )

        return True, "Pagamento confirmado e saldo atualizado!"

    except Exception:
        logger.exception("Erro ao verificar pagamento PIX.")
        return False, "Erro ao consultar o pagamento. Tente novamente."


async def botoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    dados = query.data or ""

    if dados == "inicio":
        await query.edit_message_text(
            "🇧🇷 *DZ E-SIM*\n\nEscolha uma opção:",
            reply_markup=montar_menu(),
            parse_mode="Markdown",
        )
        return

    if dados == "meu_saldo":
        saldo = consultar_saldo(query.from_user.id)
        await query.edit_message_text(
            f"💰 *Meu saldo*\n\nSaldo atual: *{dinheiro(saldo)}*",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "💳 Recarregar via PIX",
                    callback_data="recarregar_pix",
                )],
                [InlineKeyboardButton("🏠 Menu", callback_data="inicio")],
            ]),
            parse_mode="Markdown",
        )
        return

    if dados == "recarregar_pix":
        context.user_data["aguardando_valor_pix"] = True
        await query.edit_message_text(
            f"💳 *Recarga via PIX*\n\n"
            f"Digite o valor desejado em reais, por exemplo: `10,00`\n"
            f"Valor mínimo: *{dinheiro(VALOR_MINIMO_PIX)}*\n\n"
            "Envie o valor como mensagem neste chat.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Menu", callback_data="inicio")
            ]]),
            parse_mode="Markdown",
        )
        return

    if dados.startswith("verificar:"):
        pagamento_id = dados.split(":", 1)[1]
        ok, mensagem = await verificar_pagamento(
            pagamento_id,
            query.from_user.id,
        )
        await query.edit_message_text(
            ("✅ " if ok else "ℹ️ ") + mensagem,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💰 Meu saldo", callback_data="meu_saldo")],
                [InlineKeyboardButton("🏠 Menu", callback_data="inicio")],
            ]),
        )
        return

    if dados.startswith("paises:"):
        try:
            pagina = max(0, int(dados.split(":")[1]))
        except (IndexError, ValueError):
            pagina = 0
        await mostrar_paises(query, pagina)
        return

    if dados.startswith("servicos:"):
        partes = dados.split(":")
        if len(partes) == 3:
            pais = partes[1]
            try:
                pagina = max(0, int(partes[2]))
            except ValueError:
                pagina = 0
            await mostrar_catalogo(query, pais, pagina)
        return

    if dados.startswith("detalhe:"):
        partes = dados.split(":", 2)
        if len(partes) == 3:
            await query.edit_message_text(
                f"ℹ️ Serviço: {partes[2]}\n"
                f"País: {partes[1]}\n\n"
                "Esta consulta é apenas informativa. Compra e entrega "
                "de números ainda não estão habilitadas.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "⬅️ Voltar ao catálogo",
                        callback_data=f"servicos:{partes[1]}:0",
                    )
                ], [
                    InlineKeyboardButton("🏠 Menu", callback_data="inicio")
                ]]),
            )


async def receber_valor_pix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("aguardando_valor_pix"):
        return

    texto = (update.effective_message.text or "").strip()
    valor_texto = texto.replace("R$", "").replace(" ", "").replace(",", ".")

    try:
        valor = round(float(valor_texto), 2)
    except ValueError:
        await update.effective_message.reply_text(
            "Digite um valor válido, por exemplo: 10,00"
        )
        return

    if valor < VALOR_MINIMO_PIX:
        await update.effective_message.reply_text(
            f"A recarga mínima é {dinheiro(VALOR_MINIMO_PIX)}."
        )
        return

    if valor > 10000:
        await update.effective_message.reply_text(
            "Para sua segurança, o limite por recarga é R$ 10.000,00."
        )
        return

    context.user_data["aguardando_valor_pix"] = False

    cobranca, erro = await criar_cobranca_pix(valor, update.effective_user.id)

    if erro:
        await update.effective_message.reply_text(erro)
        return

    await update.effective_message.reply_text(
        f"💳 *PIX para recarga*\n\n"
        f"Valor: *{dinheiro(cobranca['valor'])}*\n\n"
        f"Copie e cole o código PIX abaixo no aplicativo do seu banco:\n\n"
        f"`{cobranca['codigo']}`\n\n"
        "Depois de pagar, toque em “Verificar pagamento”. "
        "O saldo só será creditado após a confirmação do Mercado Pago.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🔎 Verificar pagamento",
                callback_data=f"verificar:{cobranca['id']}",
            )],
            [InlineKeyboardButton("💰 Meu saldo", callback_data="meu_saldo")],
            [InlineKeyboardButton("🏠 Menu", callback_data="inicio")],
        ]),
        parse_mode="Markdown",
    )


async def erro_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(
        "Erro durante o processamento da atualização.",
        exc_info=context.error,
    )


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "Configure TELEGRAM_BOT_TOKEN nas variáveis de ambiente."
        )

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(botoes))
    app.add_handler(
        # Só captura mensagens de texto; não interfere nos comandos.
        __import__("telegram.ext", fromlist=["MessageHandler"]).MessageHandler(
            __import__("telegram.ext", fromlist=["filters"]).filters.TEXT
            & ~__import__("telegram.ext", fromlist=["filters"]).filters.COMMAND,
            receber_valor_pix,
        )
    )
    app.add_error_handler(erro_handler)

    logger.info("DZ E-SIM iniciado.")
    app.run_polling()


if __name__ == "__main__":
    main()
