"""Klima Comment/DM Watcher (V.0.2.33) — roda no Termux, 3º processo (junto
com bridge.mjs e instagram_bot.py), MESMA sessão de Instagram já logada
(reaproveita session.json do instagram_bot.py — não precisa logar de novo).

DESENHO DE SEGURANÇA (pedido explícito): o bot NUNCA manda DM pra quem não
mandou mensagem primeiro. O fluxo é:
  1. Vigia os comentários dos posts recentes do Klima.
  2. Quando alguém comenta a palavra-gatilho (ex: "PDF"), responde o
     comentário PUBLICAMENTE pedindo pra pessoa mandar DM — isso faz a
     pessoa "puxar" a conversa, em vez do bot iniciar (a ação que mais
     pesa no risco de bloqueio é o bot mandar a PRIMEIRA mensagem).
  3. Só quando a pessoa manda uma DM de volta, o bot responde com o link —
     e só se a trava de segurança do servidor (limite/dia, limite/hora,
     dia de descanso) permitir (ver /api/dm/pode-enviar).

⚠️ Ainda assim, isso é automação NÃO-OFICIAL (instagrapi, não a API do
Meta) — reduz o risco, não elimina. Prefira a automação nativa do
Instagram (Meta Business Suite → Automações → "Comentário para Mensagem")
quando possível; use isso só se precisar de 100% automático mesmo.

CONTRATO com o Klima:
  GET  /api/dm/posts-recentes?secret=...  -> { posts: [{instagram_media_id, article_id, link}] }
  GET  /api/dm/pode-enviar?secret=...     -> { pode, motivo, gatilho, mensagens[], delay_min_segundos, delay_max_segundos }
  POST /api/dm/confirmar                  -> { username, gatilho, post_article_id, ok, erro }

CONFIGURAÇÃO (variáveis de ambiente — mesmo padrão dos outros 2 bridges):
  KLIMA_API_BASE      — ex.: "https://klima-news-monitor.onrender.com"
  DM_BRIDGE_SECRET     — token secreto compartilhado com o Klima
  POLL_INTERVAL_SECONDS — opcional, default 120 (2min — não precisa ser rápido)

Usuário/senha do Instagram: reaproveita o MESMO session.json que o
instagram_bot.py já gerou — rode este script só DEPOIS do primeiro login
do instagram_bot.py já ter dado certo.
"""

import json
import logging
import os
import random
import sys
import time
from pathlib import Path

import requests

try:
    from instagrapi import Client
except ImportError:
    print("Falta instalar o instagrapi — veja o README.")
    sys.exit(1)

HERE = Path(__file__).resolve().parent
SESSION_FILE = HERE / "session.json"  # mesmo arquivo do instagram_bot.py
LOG_FILE = HERE / "klima_execution.log"
ESTADO_FILE = HERE / "dm_watcher_state.json"  # comentários/DMs já processados + mapa username->link

KLIMA_API_BASE = os.environ.get("KLIMA_API_BASE", "").rstrip("/")
BRIDGE_SECRET = os.environ.get("DM_BRIDGE_SECRET", "")
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "120"))

if not KLIMA_API_BASE or not BRIDGE_SECRET:
    print("Faltam variáveis de ambiente: KLIMA_API_BASE e/ou DM_BRIDGE_SECRET.")
    sys.exit(1)

logging.basicConfig(
    filename=str(LOG_FILE), level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s",
)


def log(mensagem, nivel="info"):
    print(f"[{nivel.upper()}] {mensagem}")
    getattr(logging, nivel if nivel in ("info", "warning", "error") else "info")(mensagem)
    try:
        requests.post(
            f"{KLIMA_API_BASE}/api/instagram/log",  # mesmo log unificado do bot de posts
            json={"secret": os.environ.get("INSTAGRAM_BRIDGE_SECRET", ""),
                  "mensagem": f"[DM watcher] {mensagem}", "nivel": nivel},
            timeout=10,
        )
    except Exception:
        pass


def carregar_estado():
    if ESTADO_FILE.exists():
        try:
            return json.loads(ESTADO_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"comentarios_vistos": [], "mensagens_vistas": [], "usuario_para_link": {}}


def salvar_estado(estado):
    # Mantém as listas de "vistos" com tamanho limitado (não crescer pra sempre).
    estado["comentarios_vistos"] = estado["comentarios_vistos"][-500:]
    estado["mensagens_vistas"] = estado["mensagens_vistas"][-500:]
    ESTADO_FILE.write_text(json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")


def _obter_client():
    if not SESSION_FILE.exists():
        raise RuntimeError("session.json não existe ainda — rode o instagram_bot.py primeiro e faça o login.")
    cl = Client()
    cl.load_settings(str(SESSION_FILE))
    cl.get_timeline_feed()  # confirma que a sessão está válida
    return cl


def buscar_posts_recentes():
    resp = requests.get(f"{KLIMA_API_BASE}/api/dm/posts-recentes",
                         params={"secret": BRIDGE_SECRET}, timeout=15)
    resp.raise_for_status()
    return resp.json().get("posts") or []


def consultar_pode_enviar():
    resp = requests.get(f"{KLIMA_API_BASE}/api/dm/pode-enviar",
                         params={"secret": BRIDGE_SECRET}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def confirmar_dm(username, gatilho, post_article_id, ok, erro=None):
    try:
        requests.post(
            f"{KLIMA_API_BASE}/api/dm/confirmar",
            json={"secret": BRIDGE_SECRET, "username": username, "gatilho": gatilho,
                  "post_article_id": post_article_id, "ok": ok, "erro": erro},
            timeout=15,
        )
    except Exception as exc:
        log(f"Falha ao confirmar DM pro Klima: {exc}", nivel="error")


def vigiar_comentarios(cl, estado, posts, gatilho):
    """Passo 1+2: acha comentário novo com a palavra-gatilho, responde
    PUBLICAMENTE (nunca manda DM aqui) e guarda o link daquele post pro
    username, pra usar quando a pessoa mandar DM de volta."""
    for post in posts:
        media_id = post.get("instagram_media_id")
        link = post.get("link")
        if not media_id or not link:
            continue
        try:
            comentarios = cl.media_comments(media_id, amount=30)
        except Exception as exc:
            log(f"Erro ao buscar comentários do post {media_id}: {exc}", nivel="error")
            continue
        for c in comentarios:
            comment_id = str(c.pk)
            if comment_id in estado["comentarios_vistos"]:
                continue
            estado["comentarios_vistos"].append(comment_id)
            texto = (c.text or "").lower()
            if gatilho.lower() not in texto:
                continue
            username = c.user.username
            estado["usuario_para_link"][username] = {"link": link, "article_id": post.get("article_id")}
            try:
                time.sleep(random.randint(3, 8))
                cl.comment(media_id, f"@{username} Te mandei no direct! Se não chegar, manda uma mensagem "
                                      f"aqui pelo direct que eu te respondo 😉")
                log(f"Respondi comentário de @{username} (gatilho '{gatilho}') no post {media_id}.")
            except Exception as exc:
                log(f"Falha ao responder comentário de @{username}: {exc}", nivel="error")
    salvar_estado(estado)


def vigiar_dms(cl, estado, gatilho):
    """Passo 3: só responde DM de quem JÁ está no mapa (comentou antes) —
    nunca inicia conversa com ninguém. Verifica a trava do servidor antes
    de cada envio."""
    try:
        threads = cl.direct_threads(amount=20)
    except Exception as exc:
        log(f"Erro ao buscar caixa de entrada de DMs: {exc}", nivel="error")
        return

    for thread in threads:
        try:
            usuarios = [u.username for u in thread.users]
        except Exception:
            continue
        candidatos = [u for u in usuarios if u in estado["usuario_para_link"]]
        if not candidatos:
            continue
        username = candidatos[0]
        info = estado["usuario_para_link"].get(username)
        if not info:
            continue

        mensagens = getattr(thread, "messages", None) or []
        for msg in mensagens:
            msg_id = str(getattr(msg, "id", "") or getattr(msg, "pk", ""))
            if not msg_id or msg_id in estado["mensagens_vistas"]:
                continue
            estado["mensagens_vistas"].append(msg_id)
            # Só reage a mensagem que NÃO foi mandada pela própria conta do Klima.
            if str(getattr(msg, "user_id", "")) == str(cl.user_id):
                continue

            status = consultar_pode_enviar()
            if not status.get("pode"):
                log(f"DM pra @{username} adiada: {status.get('motivo')}", nivel="warning")
                continue

            modelo = random.choice(status.get("mensagens") or ["Aqui está: {link}"])
            texto = modelo.replace("{link}", info["link"])
            delay = random.randint(
                status.get("delay_min_segundos", 240), status.get("delay_max_segundos", 540),
            )
            log(f"Aguardando {delay}s antes de responder DM de @{username}...")
            time.sleep(delay)
            try:
                cl.direct_send(texto, user_ids=[thread.users[0].pk] if thread.users else [])
                confirmar_dm(username, gatilho, info.get("article_id"), True)
                log(f"✅ DM respondida pra @{username}.")
                del estado["usuario_para_link"][username]  # já resolvido, não guarda pra sempre
            except Exception as exc:
                confirmar_dm(username, gatilho, info.get("article_id"), False, str(exc))
                log(f"❌ Falha ao mandar DM pra @{username}: {exc}", nivel="error")
    salvar_estado(estado)


def main():
    log(f"Iniciando o DM watcher (poll a cada {POLL_INTERVAL_SECONDS}s)...")
    estado = carregar_estado()
    while True:
        try:
            cl = _obter_client()
            status = consultar_pode_enviar()
            gatilho = status.get("gatilho", "PDF")
            posts = buscar_posts_recentes()
            vigiar_comentarios(cl, estado, posts, gatilho)
            vigiar_dms(cl, estado, gatilho)
        except Exception as exc:
            log(f"Erro inesperado no loop principal: {exc}", nivel="error")
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Encerrando (Ctrl+C).")
