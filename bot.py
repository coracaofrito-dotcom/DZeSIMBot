import os
import logging
import requests
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from saldo import (
    consultar_saldo,
    creditar_saldo,
    registrar_cobranca,
    consultar_cobranca,
    marcar_cobranca_paga,
)

from paises import buscar_paises

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)

VALOR_MINIMO_PIX = 5.00
MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN")

URL_PRODUTOS = "https://5sim.net/v1/guest/products/"
URL_CAMBIO = "https://api.frankfurter.dev/v1/latest"

LUCRO_WHATSAPP = 10.00
LUCRO_TELEGRAM = 3.00
LUCRO_SMS = 2.00

SERVICOS = {
    "whatsapp": "WhatsApp",
    "telegram": "Telegram",
    "sms": "SMS",
}


def obter_cambio():
    try:
        r = requests.get(
            URL_CAMBIO,
            params={"from": "USD", "to": "BRL"},
            timeout=15,
        )
        r.raise_for_status()
        return float(r.json()["rates"]["BRL"])
    except (requests.RequestException, KeyError, ValueError, TypeError):
        return None


def buscar_produtos(pais="brazil", operadora="any"):
    """Busca os produtos de um país e operadora na API pública da 5SIM."""
    url = f"https://5sim.net/v1/guest/products/{pais}/{operadora}"
    try:
        resposta = requests.get(url, timeout=20)
        resposta.raise_for_status()
        dados = resposta.json()
        return dados if isinstance(dados, dict) else {}
    except (requests.RequestException, ValueError) as erro:
        logging.warning("Erro ao buscar produtos: %s", erro)
        return {}


def encontrar_servicos(produtos, pais="brazil"):
    """Retorna todos os serviços do catálogo, não apenas três nomes."""
    encontrados = []
    if not isinstance(produtos, dict):
        return encontrados

    for servico, detalhes in produtos.items():
        if not isinstance(detalhes, dict):
            continue
        try:
            preco = float(detalhes.get("Price", 0) or 0)
            quantidade = int(detalhes.get("Qty", 0) or 0)
        except (TypeError, ValueError):
            continue

        if preco >= 0 and quantidade > 0:
            encontrados.append((pais, str(servico), detalhes))

    return sorted(encontrados, key=lambda item: item[1].lower())


def montar_menu():
    teclado = [
        [
            InlineKeyboardButton(
                "📱 Catálogo do Brasil (preços)",
                callback_data="servicos:brazil:0",
            )
        ],
        [
            InlineKeyboardButton(
                "🌎 Países disponíveis",
                callback_data="paises:0",
            )
        ],
        [
            InlineKeyboardButton(
                "💰 Meu saldo",
                callback_data="meu_saldo",
            )
        ],
        [
            InlineKeyboardButton(
                "💳 Recarregar saldo via PIX",
                callback_data="recarregar_pix",
            )
        ],
    ]
    return InlineKeyboardMarkup(teclado)


def listar_paises():
    """Obtém os países que a API pública da 5SIM disponibiliza."""
    try:
        r = requests.get("https://5sim.net/v1/guest/countries", timeout=20)
        r.raise_for_status()
        dados = r.json()
        return sorted(dados.keys()) if isinstance(dados, dict) else []
    except (requests.RequestException, ValueError):
        logging.warning("Não foi possível consultar os países da 5SIM.")
        return []


async def mostrar_paises(query, pagina=0):
    paises = listar_paises()
    if not paises:
        await query.edit_message_text(
            "Não consegui consultar os países agora. Tente novamente mais tarde."
        )
        return

    por_pagina = 8
    inicio = pagina * por_pagina
    lote = paises[inicio:inicio + por_pagina]
    teclado = []

    for codigo in lote:
        nome = codigo.replace("and", " and ").replace("-", " ").title()
        teclado.append([
            InlineKeyboardButton(nome, callback_data=f"servicos:{codigo}:0")
        ])

    navegacao = []
    if pagina > 0:
        navegacao.append(
            InlineKeyboardButton("⬅️ Anterior", callback_data=f"paises:{pagina - 1}")
        )
    if inicio + por_pagina < len(paises):
        navegacao.append(
            InlineKeyboardButton("Próximos ➡️", callback_data=f"paises:{pagina + 1}")
        )
    if navegacao:
        teclado.append(navegacao)
    teclado.append([InlineKeyboardButton("🏠 Menu", callback_data="inicio")])

    await query.edit_message_text(
        f"🌎 Escolha um país ({len(paises)} disponíveis)\n"
        f"Página {pagina + 1} de {(len(paises) + por_pagina - 1) // por_pagina}:",
        reply_markup=InlineKeyboardMarkup(teclado),
    )


async def mostrar_catalogo(query, pais, pagina=0):
    await query.edit_message_text("⏳ Consultando serviços, preços e estoque...")
    produtos = buscar_produtos(pais, "any")
    cambio = obter_cambio()

    itens = encontrar_servicos(produtos, pais)
    if not itens:
        await query.edit_message_text(
            f"Não encontrei serviços com estoque para {pais}.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Países", callback_data="paises:0")
            ]]),
        )
        return

    por_pagina = 8
    total_paginas = (len(itens) + por_pagina - 1) // por_pagina
    pagina = max(0, min(pagina, total_paginas - 1))
    lote = itens[pagina * por_pagina:(pagina + 1) * por_pagina]

    linhas = [
        f"📋 Catálogo de serviços — {pais.title()}",
        f"📦 {len(itens)} serviços com estoque",
        "Preços estimados; nenhum pedido será comprado automaticamente.",
        "",
    ]
    teclado = []

    for _, nome, dados in lote:
        try:
            usd = float(dados.get("Price", 0))
            qtd = int(dados.get("Qty", 0))
        except (TypeError, ValueError):
            continue

        # Mantém as margens já configuradas para WhatsApp e Telegram;
        # para os outros serviços aplica a margem padrão de R$ 2.
        margens = {
            "whatsapp": LUCRO_WHATSAPP,
            "telegram": LUCRO_TELEGRAM,
            "sms": LUCRO_SMS,
        }
        margem = margens.get(nome.lower(), 2.00)

        if cambio is None:
            preco_texto = "Preço temporariamente indisponível"
        else:
            preco_texto = f"R$ {usd * cambio + margem:.2f}"

        linhas.append(f"• {nome} — {preco_texto} | estoque: {qtd}")
        teclado.append([
            InlineKeyboardButton(
                f"ℹ️ {nome[:45]}",
                callback_data=f"detalhe:{pais}:{nome}",
            )
        ])

    navegacao = []
    if pagina > 0:
        navegacao.append(InlineKeyboardButton(
            "⬅️ Anterior", callback_data=f"servicos:{pais}:{pagina - 1}"
        ))
    if pagina + 1 < total_paginas:
        navegacao.append(InlineKeyboardButton(
            "Próxima ➡️", callback_data=f"servicos:{pais}:{pagina + 1}"
        ))
    if navegacao:
        teclado.append(navegacao)
    teclado.append([InlineKeyboardButton("🌎 Trocar país", callback_data="paises:0")])

    linhas.append("")
    linhas.append(f"Página {pagina + 1} de {total_paginas}")
    await query.edit_message_text(
        "\n".join(linhas)[:4000],
        reply_markup=InlineKeyboardMarkup(teclado),
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensagem = (
        "👋 Bem-vindo ao DZ E-SIM!\n\n"
        "Escolha um serviço para consultar o catálogo de preços.\n"
        "Este catálogo é informativo; nenhuma compra será realizada aqui."
    )
    if update.message:
        await update.message.reply_text(mensagem, reply_markup=montar_menu())


async def botoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    escolha = query.data or ""

    if escolha == "inicio":
        await query.edit_message_text(
            "👋 Bem-vindo ao DZ E-SIM!\nEscolha uma opção:",
            reply_markup=montar_menu(),
        )
    elif escolha.startswith("paises:"):
        try:
            pagina = int(escolha.split(":")[1])
        except (ValueError, IndexError):
            pagina = 0
        await mostrar_paises(query, pagina)
    elif escolha.startswith("servicos:"):
        partes = escolha.split(":")
        if len(partes) == 3:
            try:
                pagina = int(partes[2])
            except ValueError:
                pagina = 0
            await mostrar_catalogo(query, partes[1], pagina)
    elif escolha.startswith("detalhe:"):
        partes = escolha.split(":", 2)
        if len(partes) == 3:
            pais, nome = partes[1], partes[2]
            produtos = buscar_produtos(pais, "any")
            dados = produtos.get(nome, {})
            if isinstance(dados, dict):
                try:
                    usd = float(dados.get("Price", 0))
                    qtd = int(dados.get("Qty", 0))
                    cambio = obter_cambio()
                    margem = {
                        "whatsapp": LUCRO_WHATSAPP,
                        "telegram": LUCRO_TELEGRAM,
                        "sms": LUCRO_SMS,
                    }.get(nome.lower(), 2.00)
                    preco = (
                        f"R$ {usd * cambio + margem:.2f}"
                        if cambio is not None
                        else "Preço temporariamente indisponível"
                    )
                    texto = (
                        f"📋 {nome}\n🌎 País: {pais}\n"
                        f"💰 Preço estimado: {preco}\n"
                        f"📦 Estoque informado: {qtd}\n\n"
                        "ℹ️ Consulta informativa. Compra e entrega ainda não estão habilitadas."
                    )
                except (TypeError, ValueError):
                    texto = "Não consegui ler o preço deste serviço."
            else:
                texto = "Serviço não encontrado no catálogo atual."
            await query.edit_message_text(
                texto,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "⬅️ Voltar ao catálogo",
                        callback_data=f"servicos:{pais}:0",
                    )
                ]]),
            )


async def erro_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.error("Erro ao processar atualização: %s", context.error)


def verificar_pagamento(pagamento_id, telegram_id):
    import mercadopago

    if not MP_ACCESS_TOKEN:
        return {"erro": "Mercado Pago não configurado."}

    cobranca = consultar_cobranca(pagamento_id)
    if not cobranca or cobranca["telegram_id"] != telegram_id:
        return {"erro": "Cobrança não encontrada para este usuário."}

    if cobranca["status"] == "paga":
        return {"aprovado": True, "ja_creditado": True}

    try:
        sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
        resultado = sdk.payment().get(str(pagamento_id))

        if resultado.get("status") != 200:
            return {"erro": "Não foi possível consultar o pagamento."}

        pagamento = resultado.get("response", {})

        if pagamento.get("status") != "approved":
            return {"aprovado": False}

        referencia = str(pagamento.get("external_reference", ""))
        valor_pago = round(float(pagamento.get("transaction_amount", 0)), 2)

        if referencia != cobranca["referencia"]:
            return {"erro": "A referência do pagamento não confere."}

        if valor_pago != round(cobranca["valor"], 2):
            return {"erro": "O valor pago não confere."}

        creditou = creditar_saldo(
            telegram_id,
            valor_pago,
            f"mercadopago:{pagamento_id}",
        )

        if not creditou:
            return {"erro": "Não foi possível registrar o crédito."}

        marcar_cobranca_paga(pagamento_id)

        return {
            "aprovado": True,
            "valor": valor_pago,
            "ja_creditado": False,
        }

    except Exception:
        logging.exception("Erro ao verificar pagamento PIX.")
        return {"erro": "Falha ao consultar o Mercado Pago."}


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "A variável TELEGRAM_BOT_TOKEN não está configurada no terminal."
        )

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(botoes))
    app.add_error_handler(erro_handler)

    print("DZ E-SIM iniciado. Aguardando mensagens...")
    app.run_polling()


if __name__ == "__main__":
    main()


def criar_cobranca_pix(valor, descricao, referencia):
    """Cria uma cobrança PIX e retorna os dados necessários para o pagamento."""
    import mercadopago

    try:
        valor = round(float(valor), 2)
    except (TypeError, ValueError):
        return {"erro": "Valor inválido."}

    if valor < VALOR_MINIMO_PIX:
        return {"erro": "O valor mínimo para pagamento PIX é R$ 5,00."}

    if not MP_ACCESS_TOKEN:
        return {"erro": "Mercado Pago não configurado."}

    try:
        sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
        pagamento = sdk.payment().create({
            "transaction_amount": valor,
            "description": str(descricao)[:200],
            "payment_method_id": "pix",
            "external_reference": str(referencia)[:100],
            "payer": {
                "email": os.getenv("MP_PAYER_EMAIL", "test_user_123456@testuser.com"),
            },
        })

        resposta = pagamento.get("response", {})
        if pagamento.get("status") not in (200, 201):
            return {"erro": "Não foi possível criar a cobrança PIX."}

        dados_pix = resposta.get("point_of_interaction", {}).get(
            "transaction_data", {}
        )
        codigo = dados_pix.get("qr_code")
        imagem_base64 = dados_pix.get("qr_code_base64")

        if not resposta.get("id") or not codigo:
            return {"erro": "O Mercado Pago não retornou os dados PIX esperados."}

        return {
            "id": resposta["id"],
            "status": resposta.get("status", "pending"),
            "valor": valor,
            "codigo_pix": codigo,
            "qr_code_base64": imagem_base64,
            "referencia": str(referencia),
        }
    except Exception:
        logging.exception("Falha ao criar cobrança PIX.")
        return {"erro": "Erro ao comunicar com o Mercado Pago."}
