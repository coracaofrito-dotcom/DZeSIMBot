
import sqlite3
from pathlib import Path

BANCO = Path(__file__).with_name("dz_esim.db")


def conectar():
    con = sqlite3.connect(BANCO, timeout=30)
    con.execute("PRAGMA foreign_keys = ON")
    return con


def inicializar_banco():
    with conectar() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS clientes (
                telegram_id INTEGER PRIMARY KEY,
                saldo REAL NOT NULL DEFAULT 0 CHECK (saldo >= 0)
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS movimentacoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                tipo TEXT NOT NULL,
                valor REAL NOT NULL CHECK (valor > 0),
                referencia TEXT UNIQUE NOT NULL,
                data DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS cobrancas_pix (
                pagamento_id TEXT PRIMARY KEY,
                telegram_id INTEGER NOT NULL,
                valor REAL NOT NULL CHECK (valor >= 5),
                referencia TEXT UNIQUE NOT NULL,
                status TEXT NOT NULL DEFAULT 'pendente'
            )
        """)


def consultar_saldo(telegram_id):
    with conectar() as con:
        con.execute(
            "INSERT OR IGNORE INTO clientes (telegram_id) VALUES (?)",
            (telegram_id,)
        )

        resultado = con.execute(
            "SELECT saldo FROM clientes WHERE telegram_id = ?",
            (telegram_id,)
        ).fetchone()

        return round(float(resultado[0]), 2)


def creditar_saldo(telegram_id, valor, referencia):
    valor = round(float(valor), 2)
    referencia = str(referencia).strip()

    if valor <= 0 or not referencia:
        return False

    with conectar() as con:
        cursor = con.execute("""
            INSERT OR IGNORE INTO movimentacoes
                (telegram_id, tipo, valor, referencia)
            VALUES (?, 'recarga_pix', ?, ?)
        """, (telegram_id, valor, referencia))

        if cursor.rowcount != 1:
            return False

        con.execute("""
            INSERT OR IGNORE INTO clientes (telegram_id, saldo)
            VALUES (?, 0)
        """, (telegram_id,))

        con.execute("""
            UPDATE clientes
            SET saldo = saldo + ?
            WHERE telegram_id = ?
        """, (valor, telegram_id))

    return True


def registrar_cobranca(telegram_id, pagamento_id, valor, referencia):
    pagamento_id = str(pagamento_id).strip()
    referencia = str(referencia).strip()
    valor = round(float(valor), 2)

    if not pagamento_id or not referencia or valor < 5:
        return False

    with conectar() as con:
        cursor = con.execute("""
            INSERT OR IGNORE INTO cobrancas_pix
                (pagamento_id, telegram_id, valor, referencia)
            VALUES (?, ?, ?, ?)
        """, (pagamento_id, telegram_id, valor, referencia))

        return cursor.rowcount == 1


def consultar_cobranca(pagamento_id):
    with conectar() as con:
        linha = con.execute("""
            SELECT telegram_id, valor, referencia, status
            FROM cobrancas_pix
            WHERE pagamento_id = ?
        """, (str(pagamento_id),)).fetchone()

    if not linha:
        return None

    return {
        "telegram_id": linha[0],
        "valor": float(linha[1]),
        "referencia": linha[2],
        "status": linha[3],
    }


def marcar_cobranca_paga(pagamento_id):
    with conectar() as con:
        cursor = con.execute("""
            UPDATE cobrancas_pix
            SET status = 'paga'
            WHERE pagamento_id = ?
              AND status = 'pendente'
        """, (str(pagamento_id),))

        return cursor.rowcount == 1


inicializar_banco()
