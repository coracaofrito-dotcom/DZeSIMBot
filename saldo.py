
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
            (int(telegram_id),)
        )
        linha = con.execute(
            "SELECT saldo FROM clientes WHERE telegram_id = ?",
            (int(telegram_id),)
        ).fetchone()
        return round(float(linha[0]), 2)


def creditar_saldo(telegram_id, valor, referencia):
    valor = round(float(valor), 2)
    referencia = str(referencia).strip()

    if valor <= 0 or not referencia:
        return False

    with conectar() as con:
        cursor = con.execute("""
            INSERT OR IGNORE INTO movimentacoes
                (telegram_id, tipo, valor, referencia)
            VALUES (?, 'ajuste', ?, ?)
        """, (int(telegram_id), valor, referencia))

        if cursor.rowcount != 1:
            return False

        con.execute("""
            INSERT OR IGNORE INTO clientes (telegram_id, saldo)
            VALUES (?, 0)
        """, (int(telegram_id),))

        con.execute("""
            UPDATE clientes SET saldo = saldo + ?
            WHERE telegram_id = ?
        """, (valor, int(telegram_id)))

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
        """, (pagamento_id, int(telegram_id), valor, referencia))
        return cursor.rowcount == 1


def consultar_cobranca(pagamento_id):
    with conectar() as con:
        linha = con.execute("""
            SELECT telegram_id, valor, referencia, status
            FROM cobrancas_pix WHERE pagamento_id = ?
        """, (str(pagamento_id),)).fetchone()

    if not linha:
        return None

    return {
        "telegram_id": int(linha[0]),
        "valor": round(float(linha[1]), 2),
        "referencia": linha[2],
        "status": linha[3],
    }


def confirmar_pagamento(pagamento_id, valor_confirmado, referencia_confirmada):
    """
    Confirma e credita uma cobrança uma única vez.
    O chamador deve validar o status aprovado na API do Mercado Pago.
    """
    pagamento_id = str(pagamento_id).strip()
    referencia_confirmada = str(referencia_confirmada or "").strip()
    valor_confirmado = round(float(valor_confirmado), 2)

    if not pagamento_id or not referencia_confirmada:
        return False

    with conectar() as con:
        cobranca = con.execute("""
            SELECT telegram_id, valor, referencia, status
            FROM cobrancas_pix WHERE pagamento_id = ?
        """, (pagamento_id,)).fetchone()

        if not cobranca:
            return False

        telegram_id, valor_esperado, referencia_esperada, status = cobranca
        valor_esperado = round(float(valor_esperado), 2)

        if status == "paga":
            return True

        if status != "pendente":
            return False

        if referencia_confirmada != referencia_esperada:
            return False

        if valor_confirmado != valor_esperado:
            return False

        referencia_movimento = f"pix:{pagamento_id}"

        cursor = con.execute("""
            INSERT OR IGNORE INTO movimentacoes
                (telegram_id, tipo, valor, referencia)
            VALUES (?, 'recarga_pix', ?, ?)
        """, (telegram_id, valor_esperado, referencia_movimento))

        if cursor.rowcount != 1:
            return False

        con.execute("""
            INSERT OR IGNORE INTO clientes (telegram_id, saldo)
            VALUES (?, 0)
        """, (telegram_id,))

        con.execute("""
            UPDATE clientes SET saldo = saldo + ?
            WHERE telegram_id = ?
        """, (valor_esperado, telegram_id))

        con.execute("""
            UPDATE cobrancas_pix SET status = 'paga'
            WHERE pagamento_id = ? AND status = 'pendente'
        """, (pagamento_id,))

    return True


def marcar_cobranca_paga(pagamento_id):
    # Não marca como paga sem creditar o saldo de forma atômica.
    return False


inicializar_banco()
