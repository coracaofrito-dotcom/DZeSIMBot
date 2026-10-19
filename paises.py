import requests

URL_PAISES = "https://5sim.net/v1/guest/countries"


def buscar_paises():
    """Busca os países disponíveis na API pública da 5SIM."""
    try:
        resposta = requests.get(URL_PAISES, timeout=20)
        resposta.raise_for_status()
        dados = resposta.json()

        if not isinstance(dados, dict):
            print("A API retornou um formato inesperado.")
            return {}

        return dados

    except requests.RequestException as erro:
        print(f"Erro ao consultar os países: {erro}")
        return {}
