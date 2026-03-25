from flask import Flask, request, jsonify
import hashlib
import json
import os
import sys
import requests
import functools
from datetime import datetime
import psycopg2
print = functools.partial(print, flush=True)

app = Flask(__name__)

BOT_TOKEN = "8739879398:AAHqr2kXTAEySZ3D8O6hEvsnIXYOMpwBAIU"
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
DATABASE_URL = os.environ.get("DATABASE_URL", "")

def get_conn():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS licencas (
            id SERIAL PRIMARY KEY,
            hd_serial TEXT UNIQUE,
            empresa TEXT,
            usuarios INTEGER DEFAULT 1,
            status TEXT DEFAULT 'PENDENTE',
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            aprovado_em TIMESTAMP,
            dias_revalidar INTEGER DEFAULT 30,
            expiracao DATE
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS config (
            chave TEXT PRIMARY KEY,
            valor TEXT
        )
    """)
    admin_chat_id = os.environ.get("ADMIN_CHAT_ID")
    if admin_chat_id:
        c.execute("INSERT INTO config (chave, valor) VALUES (%s, %s) ON CONFLICT (chave) DO UPDATE SET valor = %s",
                  ("admin_chat_id", admin_chat_id, admin_chat_id))
    conn.commit()
    conn.close()

    # ✅ Adiciona coluna expiracao se não existir
    try:
        c.execute("ALTER TABLE licencas ADD COLUMN expiracao DATE")
        conn.commit()
    except:
        pass

def get_config(chave):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT valor FROM config WHERE chave = %s", (chave,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def enviar_telegram(chat_id, mensagem):
    print(f"ENVIANDO TELEGRAM para {chat_id}: {mensagem[:50]}", flush=True)
    r = requests.post(f"{TELEGRAM_API}/sendMessage", json={
        "chat_id": chat_id,
        "text": mensagem,
        "parse_mode": "HTML"
    })
    print(f"RESPOSTA TELEGRAM: {r.status_code} {r.text}", flush=True)

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

    conn = get_conn()
    c = conn.cursor()

    c.execute("SELECT status, usuarios, dias_revalidar, expiracao FROM licencas WHERE hd_serial = %s", (hd_serial,))
    row = c.fetchone()

    if row:
        status         = row[0]
        usuarios       = row[1]
        dias_revalidar = row[2] if row[2] else 30
        expiracao      = str(row[3]) if row[3] else None
        conn.close()
        if status == "ATIVA":
            licenca = gerar_licenca(hd_serial, usuarios)
            return jsonify({
                "ok": True,
                "status": "ATIVA",
                "usuarios": usuarios,
                "licenca": licenca,
                "dias_revalidar": dias_revalidar,
                "expiracao": expiracao
            })
        else:
            return jsonify({"ok": False, "status": status})

    c.execute("""
        INSERT INTO licencas (hd_serial, empresa, status)
        VALUES (%s, %s, 'PENDENTE')
    """, (hd_serial, empresa))
    conn.commit()
    conn.close()

    chat_id = get_config("admin_chat_id")
    if chat_id:
        cnpj = data.get("cnpj", "Não informado")
        telefone = data.get("telefone", "Não informado")
        cidade = data.get("cidade", "Não informado")
        estado = data.get("estado", "Não informado")
        mensagem = (
            f"🆕 <b>Nova solicitação de licença</b>\n\n"
            f"🏢 <b>Empresa:</b> {empresa}\n"
            f"📄 <b>CNPJ:</b> {cnpj}\n"
            f"📍 <b>Cidade:</b> {cidade}/{estado}\n"
            f"📞 <b>Telefone:</b> {telefone}\n"
            f"💾 <b>HD Serial:</b> <code>{hd_serial}</code>\n\n"
            f"Para aprovar 7 dias de teste:\n"
            f"<code>/expiracao {hd_serial} {(datetime.now() + __import__('datetime').timedelta(days=7)).strftime('%Y-%m-%d')} 1</code>\n\n"
            f"Para aprovar licença completa:\n"
            f"<code>/expiracao {hd_serial} AAAA-MM-DD NUM_USUARIOS</code>"
        )
        enviar_telegram(chat_id, mensagem)

    return jsonify({"ok": False, "status": "PENDENTE"})

@app.route("/validar", methods=["POST"])
def validar():
    data      = request.json
    hd_serial = data.get("hd_serial")
    licenca   = data.get("licenca")

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT status, usuarios, dias_revalidar, expiracao FROM licencas WHERE hd_serial = %s", (hd_serial,))
    row = c.fetchone()
    conn.close()

    if not row or row[0] != "ATIVA":
        return jsonify({"ok": False})

    usuarios       = row[1]
    dias_revalidar = row[2] if row[2] else 30
    expiracao      = str(row[3]) if row[3] else None
    licenca_esperada = gerar_licenca(hd_serial, usuarios)

    if licenca != licenca_esperada:
        return jsonify({"ok": False})

    return jsonify({"ok": True, "usuarios": usuarios, "dias_revalidar": dias_revalidar, "expiracao": expiracao})

@app.route("/renovar", methods=["POST"])
def renovar():
    data      = request.json
    hd_serial = data.get("hd_serial")
    empresa   = data.get("empresa", "Não informado")

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT status, usuarios, expiracao FROM licencas WHERE hd_serial = %s", (hd_serial,))
    row = c.fetchone()
    conn.close()

    chat_id = get_config("admin_chat_id")
    if chat_id:
        status    = row[0] if row else "NÃO CADASTRADO"
        usuarios  = row[1] if row else "-"
        expiracao = str(row[2]) if row and row[2] else "Sem data"
        mensagem = (
            f"🔄 <b>Solicitação de Renovação</b>\n\n"
            f"🏢 <b>Empresa:</b> {empresa}\n"
            f"💾 <b>HD Serial:</b> <code>{hd_serial}</code>\n"
            f"👥 <b>Usuários atuais:</b> {usuarios}\n"
            f"📅 <b>Expiração atual:</b> {expiracao}\n\n"
            f"Para renovar, envie:\n"
            f"<code>/expiracao {hd_serial} AAAA-MM-DD {usuarios}</code>"
        )
        enviar_telegram(chat_id, mensagem)

    return jsonify({"ok": True})

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    if not data or "message" not in data:
        return "ok"

    msg     = data["message"]
    chat_id = msg["chat"]["id"]
    texto   = msg.get("text", "").strip()

    print(f"CHAT_ID: {chat_id} | TEXTO: {texto}", flush=True)

    admin_chat_id = os.environ.get("ADMIN_CHAT_ID", str(chat_id))
    print(f"ADMIN_CHAT_ID ENV: '{admin_chat_id}' | CHAT_ID: '{str(chat_id)}'", flush=True)

    if str(chat_id) != str(admin_chat_id):
        enviar_telegram(chat_id, "⛔ Acesso não autorizado.")
        return "ok"

    if texto.startswith("/aprovar"):
        partes = texto.split()
        if len(partes) < 3:
            enviar_telegram(chat_id, "❌ Uso: /aprovar [hd_serial] [num_usuarios]")
            return "ok"
        hd_serial = partes[1]
        try:
            usuarios = int(partes[2])
        except:
            enviar_telegram(chat_id, "❌ Número de usuários inválido")
            return "ok"
        conn = get_conn()
        c = conn.cursor()
        c.execute("""
            UPDATE licencas SET status = 'ATIVA', usuarios = %s, aprovado_em = %s
            WHERE hd_serial = %s
        """, (usuarios, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), hd_serial))
        afetado = c.rowcount
        conn.commit()
        conn.close()
        if afetado:
            enviar_telegram(chat_id, f"✅ Licença aprovada!\n💾 HD: {hd_serial}\n👥 Usuários: {usuarios}")
        else:
            enviar_telegram(chat_id, f"❌ HD serial não encontrado: {hd_serial}")

    elif texto.startswith("/revogar"):
        partes = texto.split()
        if len(partes) < 2:
            enviar_telegram(chat_id, "❌ Uso: /revogar [hd_serial]")
            return "ok"
        hd_serial = partes[1]
        conn = get_conn()
        c = conn.cursor()
        c.execute("UPDATE licencas SET status = 'REVOGADA' WHERE hd_serial = %s", (hd_serial,))
        conn.commit()
        conn.close()
        enviar_telegram(chat_id, f"🚫 Licença revogada: {hd_serial}")

    elif texto.startswith("/pendente"):
        partes = texto.split()
        if len(partes) < 2:
            enviar_telegram(chat_id, "❌ Uso: /pendente [hd_serial]")
            return "ok"
        hd_serial = partes[1]
        conn = get_conn()
        c = conn.cursor()
        c.execute("UPDATE licencas SET status = 'PENDENTE' WHERE hd_serial = %s", (hd_serial,))
        conn.commit()
        conn.close()
        enviar_telegram(chat_id, f"⏳ Licença colocada como PENDENTE: {hd_serial}")

    elif texto.startswith("/prazo"):
        partes = texto.split()
        if len(partes) < 3:
            enviar_telegram(chat_id, "❌ Uso: /prazo [hd_serial] [dias]")
            return "ok"
        hd_serial = partes[1]
        try:
            dias = int(partes[2])
        except:
            enviar_telegram(chat_id, "❌ Número de dias inválido")
            return "ok"
        conn = get_conn()
        c = conn.cursor()
        c.execute("UPDATE licencas SET dias_revalidar = %s WHERE hd_serial = %s", (dias, hd_serial))
        afetado = c.rowcount
        conn.commit()
        conn.close()
        if afetado:
            enviar_telegram(chat_id, f"✅ Prazo atualizado!\n💾 HD: {hd_serial}\n📅 Dias: {dias}")
        else:
            enviar_telegram(chat_id, f"❌ HD serial não encontrado: {hd_serial}")

    elif texto.startswith("/expiracao"):
        partes = texto.split()
        if len(partes) < 4:
            enviar_telegram(chat_id, "❌ Uso: /expiracao [hd_serial] [data] [usuarios]\nEx: /expiracao abc123 2026-12-31 2")
            return "ok"
        hd_serial = partes[1]
        data_exp  = partes[2]
        try:
            usuarios = int(partes[3])
            datetime.strptime(data_exp, "%Y-%m-%d")
        except:
            enviar_telegram(chat_id, "❌ Data inválida! Use o formato: AAAA-MM-DD\nEx: 2026-12-31")
            return "ok"
        conn = get_conn()
        c = conn.cursor()
        c.execute("""
            UPDATE licencas SET status = 'ATIVA', usuarios = %s, expiracao = %s, aprovado_em = %s
            WHERE hd_serial = %s
        """, (usuarios, data_exp, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), hd_serial))
        afetado = c.rowcount
        conn.commit()
        conn.close()
        if afetado:
            enviar_telegram(chat_id, f"✅ Licença atualizada!\n💾 HD: {hd_serial}\n👥 Usuários: {usuarios}\n📅 Expira em: {data_exp}")
        else:
            enviar_telegram(chat_id, f"❌ HD serial não encontrado: {hd_serial}")

    elif texto.startswith("/listar"):
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT empresa, hd_serial, usuarios, status, dias_revalidar, expiracao FROM licencas ORDER BY criado_em DESC")
        rows = c.fetchall()
        conn.close()
        if not rows:
            enviar_telegram(chat_id, "Nenhuma licença cadastrada.")
        else:
            mensagem = "📋 <b>Licenças cadastradas:</b>\n\n"
            for row in rows:
                emoji = "✅" if row[3] == "ATIVA" else "⏳" if row[3] == "PENDENTE" else "🚫"
                exp = f"\n📅 Expira: {row[5]}" if row[5] else f"\n📅 Prazo: {row[4]} dias"
                mensagem += f"{emoji} <b>{row[0]}</b>\n💾 {row[1]}\n👥 {row[2]} usuários{exp}\n\n"
            enviar_telegram(chat_id, mensagem)

    elif texto in ["/start", "/help"]:
        enviar_telegram(chat_id, (
            "🤖 <b>SISPER — Gerenciador de Licenças</b>\n\n"
            "Comandos disponíveis:\n"
            "/aprovar [hd_serial] [usuarios] — Aprovar licença\n"
            "/revogar [hd_serial] — Revogar licença\n"
            "/prazo [hd_serial] [dias] — Alterar prazo de revalidação\n"
            "/expiracao [hd_serial] [data] [usuarios] — Definir data de expiração\n"
            "/listar — Listar todas as licenças\n"
        ))

    return "ok"

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)