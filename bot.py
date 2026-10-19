import os
import logging
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from paises import buscar_paises

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)

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


def encontrar_servicos(produtos):
    """Converte a resposta da 5SIM em uma lista uniforme de ofertas."""
    encontrados = []

    if not isinstance(produtos, dict):
        return encontrados

    for servico, detalhes in produtos.items():
        nome = str(servico).lower()
        if nome in SERVICOS and isinstance(detalhes, dict):
            encontrados.append(("brazil", nome, detalhes))

    return encontrados


def montar_menu():
    teclado = [
        [InlineKeyboardButton("📱 WhatsApp", callback_data="servico:whatsapp")],
        [InlineKeyboardButton("✈️ Telegram", callback_data="servico:telegram")],
        [InlineKeyboardButton("💬 SMS", callback_data="servico:sms")],
        [InlineKeyboardButton("🌎 Países disponíveis", callback_data="paises")],
    ]
    return InlineKeyboardMarkup(teclado)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensagem = (
        "👋 Bem-vindo ao DZ E-SIM!\n\n"
        "Escolha um serviço para consultar o catálogo de preços.\n"
        "Este catálogo é informativo; nenhuma compra será realizada aqui."
    )
    if update.message:
        await update.message.reply_text(mensagem, reply_markup=montar_menu())


async def mostrar_paises(query):
    paises = buscar_paises()
    if not paises:
        await query.edit_message_text(
            "Não consegui consultar os países agora. Tente novamente mais tarde."
        )
        return

    nomes = []
    for codigo, dados in paises.items():
        if isinstance(dados, dict):
            nome = dados.get("text") or dados.get("name") or codigo
        else:
            nome = codigo
        nomes.append(str(nome))

    texto = "🌎 Países encontrados: " + str(len(nomes)) + "\n\n"
    texto += "\n".join("• " + nome for nome in nomes[:40])
    if len(nomes) > 40:
        texto += f"\n\n... e mais {len(nomes) - 40} países."
    texto += "\n\nUse /start para voltar ao menu."
    await query.edit_message_text(texto)


async def mostrar_catalogo(query, nome_servico):
    await query.edit_message_text("⏳ Consultando preços e disponibilidade...")
    produtos = buscar_produtos()
    cambio = obter_cambio()

    if not produtos:
        await query.edit_message_text(
            "Não consegui consultar o catálogo da 5SIM agora. Tente novamente mais tarde."
        )
        return

    itens = [
        item for item in encontrar_servicos(produtos)
        if item[1] == nome_servico
    ]

    if not itens:
        await query.edit_message_text(
            f"Não encontrei ofertas para {SERVICOS[nome_servico]} no catálogo atual."
        )
        return

    lucro = {
        "whatsapp": LUCRO_WHATSAPP,
        "telegram": LUCRO_TELEGRAM,
        "sms": LUCRO_SMS,
    }[nome_servico]

    linhas = [
        f"📋 Catálogo: {SERVICOS[nome_servico]}",
        "Preços estimados em reais; confirme a disponibilidade antes de comprar.",
        "",
    ]

    exibidos = 0
    for pais, servico, detalhes in itens:
        try:
            preco_usd = float(detalhes["Price"])
            quantidade = int(detalhes.get("Qty", 0))
        except (KeyError, ValueError, TypeError):
            continue

        if cambio is None:
            preco_texto = f"US$ {preco_usd:.2f} + margem R$ {lucro:.2f}"
        else:
            preco_brl = preco_usd * cambio + lucro
            preco_texto = f"R$ {preco_brl:.2f}"

        linhas.append(
            f"🌍 {pais}\n"
            f"💰 {preco_texto}\n"
            f"📦 Disponíveis: {quantidade}\n"
        )
        exibidos += 1
        if exibidos >= 15:
            break

    if not exibidos:
        await query.edit_message_text("Não encontrei preços válidos para exibir.")
        return

    linhas.append("Use /start para voltar ao menu.")
    texto = "\n".join(linhas)

    # Limite de segurança do Telegram para mensagens.
    await query.edit_message_text(texto[:4000])


async def botoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    escolha = query.data or ""
    if escolha == "paises":
        await mostrar_paises(query)
    elif escolha.startswith("servico:"):
        nome = escolha.split(":", 1)[1]
        if nome in SERVICOS:
            await mostrar_catalogo(query, nome)


async def erro_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.error("Erro ao processar atualização: %s", context.error)


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
