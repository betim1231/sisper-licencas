from flask import Flask, request, jsonify
import sqlite3
import hashlib
import json
import os
import sys
import requests
import functools
from datetime import datetime
print = functools.partial(print, flush=True)

app = Flask(__name__)

BOT_TOKEN = "8739879398:AAHqr2kXTAEySZ3D8O6hEvsnIXYOMpwBAIU"
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
SEU_CHAT_ID = None  # será preenchido automaticamente

DB = "licencas.db"

def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS licencas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hd_serial TEXT UNIQUE,
            empresa TEXT,
            usuarios INTEGER DEFAULT 1,
            status TEXT DEFAULT 'PENDENTE',
            criado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
            aprovado_em DATETIME
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS config (
            chave TEXT PRIMARY KEY,
            valor TEXT
        )
    """)
    # ✅ Salva chat_id do admin via variável de ambiente
    admin_chat_id = os.environ.get("ADMIN_CHAT_ID")
    if admin_chat_id:
        c.execute("INSERT OR REPLACE INTO config (chave, valor) VALUES (?, ?)",
                  ("admin_chat_id", admin_chat_id))
    conn.commit()
    conn.close()

def get_config(chave):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT valor FROM config WHERE chave = ?", (chave,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def set_config(chave, valor):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO config (chave, valor) VALUES (?, ?)", (chave, valor))
    conn.commit()
    conn.close()

def enviar_telegram(chat_id, mensagem):
    requests.post(f"{TELEGRAM_API}/sendMessage", json={
        "chat_id": chat_id,
        "text": mensagem,
        "parse_mode": "HTML"
    })

def gerar_licenca(hd_serial, usuarios):
    dados = f"{hd_serial}:{usuarios}:SISPER"
    return hashlib.sha256(dados.encode()).hexdigest()

@app.route("/registrar", methods=["POST"])
def registrar():
    data = request.json
    hd_serial = data.get("hd_serial")
    empresa   = data.get("empresa", "Não informado")

    if not hd_serial:
        return jsonify({"ok": False, "erro": "HD serial não informado"}), 400

    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute("SELECT status, usuarios FROM licencas WHERE hd_serial = ?", (hd_serial,))
    row = c.fetchone()

    if row:
        status   = row[0]
        usuarios = row[1]
        conn.close()
        if status == "ATIVA":
            licenca = gerar_licenca(hd_serial, usuarios)
            return jsonify({"ok": True, "status": "ATIVA", "usuarios": usuarios, "licenca": licenca})
        else:
            return jsonify({"ok": False, "status": status})

    # Novo registro
    c.execute("""
        INSERT INTO licencas (hd_serial, empresa, status)
        VALUES (?, ?, 'PENDENTE')
    """, (hd_serial, empresa))
    conn.commit()
    conn.close()

    # Notifica no Telegram
    chat_id = get_config("admin_chat_id")
    if chat_id:
        mensagem = (
            f"🆕 <b>Nova solicitação de licença</b>\n\n"
            f"🏢 <b>Empresa:</b> {empresa}\n"
            f"💾 <b>HD Serial:</b> <code>{hd_serial}</code>\n\n"
            f"Para aprovar, envie:\n"
            f"<code>/aprovar {hd_serial} 1</code>\n\n"
            f"(substitua o número pelo total de usuários)"
        )
        enviar_telegram(chat_id, mensagem)

    return jsonify({"ok": False, "status": "PENDENTE"})

@app.route("/validar", methods=["POST"])
def validar():
    data      = request.json
    hd_serial = data.get("hd_serial")
    licenca   = data.get("licenca")

    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT status, usuarios FROM licencas WHERE hd_serial = ?", (hd_serial,))
    row = c.fetchone()
    conn.close()

    if not row or row[0] != "ATIVA":
        return jsonify({"ok": False})

    usuarios = row[1]
    licenca_esperada = gerar_licenca(hd_serial, usuarios)

    if licenca != licenca_esperada:
        return jsonify({"ok": False})

    return jsonify({"ok": True, "usuarios": usuarios})

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    if not data or "message" not in data:
        return "ok"

    msg     = data["message"]
    chat_id = msg["chat"]["id"]
    texto   = msg.get("text", "").strip()

    print(f"CHAT_ID RECEBIDO: {chat_id}", flush=True)  # ✅ adiciona isso

    # Salva o chat_id do admin automaticamente
    set_config("admin_chat_id", str(chat_id))
    if texto.startswith("/aprovar"):
        partes = texto.split()
        if len(partes) < 3:
            enviar_telegram(chat_id, "❌ Uso: /aprovar <hd_serial> <num_usuarios>")
            return "ok"

        hd_serial = partes[1]
        try:
            usuarios = int(partes[2])
        except:
            enviar_telegram(chat_id, "❌ Número de usuários inválido")
            return "ok"

        conn = sqlite3.connect(DB)
        c = conn.cursor()
        c.execute("""
            UPDATE licencas SET status = 'ATIVA', usuarios = ?, aprovado_em = ?
            WHERE hd_serial = ?
        """, (usuarios, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), hd_serial))
        afetado = conn.total_changes
        conn.commit()
        conn.close()

        if afetado:
            enviar_telegram(chat_id, f"✅ Licença aprovada!\n💾 HD: {hd_serial}\n👥 Usuários: {usuarios}")
        else:
            enviar_telegram(chat_id, f"❌ HD serial não encontrado: {hd_serial}")

    elif texto.startswith("/revogar"):
        partes = texto.split()
        if len(partes) < 2:
            enviar_telegram(chat_id, "❌ Uso: /revogar <hd_serial>")
            return "ok"

        hd_serial = partes[1]
        conn = sqlite3.connect(DB)
        c = conn.cursor()
        c.execute("UPDATE licencas SET status = 'REVOGADA' WHERE hd_serial = ?", (hd_serial,))
        conn.commit()
        conn.close()
        enviar_telegram(chat_id, f"🚫 Licença revogada: {hd_serial}")

    elif texto.startswith("/listar"):
        conn = sqlite3.connect(DB)
        c = conn.cursor()
        c.execute("SELECT empresa, hd_serial, usuarios, status FROM licencas ORDER BY criado_em DESC")
        rows = c.fetchall()
        conn.close()

        if not rows:
            enviar_telegram(chat_id, "Nenhuma licença cadastrada.")
        else:
            msg = "📋 <b>Licenças cadastradas:</b>\n\n"
            for row in rows:
                emoji = "✅" if row[3] == "ATIVA" else "⏳" if row[3] == "PENDENTE" else "🚫"
                msg += f"{emoji} <b>{row[0]}</b>\n💾 {row[1]}\n👥 {row[2]} usuários\n\n"
            enviar_telegram(chat_id, msg)

    elif texto == "/start" or texto == "/help":
        enviar_telegram(chat_id, (
            "🤖 <b>SISPER — Gerenciador de Licenças</b>\n\n"
            "Comandos disponíveis:\n"
            "/aprovar <hd_serial> <usuarios> — Aprovar licença\n"
            "/revogar <hd_serial> — Revogar licença\n"
            "/listar — Listar todas as licenças\n"
        ))

    return "ok"

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5001)