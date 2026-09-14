"""Klima Instagram Bot (V.0.2.32) — roda no Termux, no mesmo celular do
bridge de WhatsApp (bridge.mjs), como um segundo processo separado (gerido
pelo pm2, junto com o "klima-whatsapp" — ver start-klima-bridge-boot.sh).

O QUE ISSO FAZ: fica perguntando pro Klima (Render), por polling, se tem um
post pendente na fila — se tiver E já passou o intervalo mínimo desde o
último post (a trava de tempo é calculada NO SERVIDOR, não aqui, pra não
depender do relógio do celular estar certo), baixa as imagens do carrossel,
publica via instagrapi e confirma de volta.

CONTRATO com o lado do Klima (já implementado em app.py):
  GET  /api/instagram/fila?secret=...          -> { posts: [...], aguardar_segundos, pausado }
  GET  /api/instagram/credenciais?secret=...    -> { username, password }
  POST /api/instagram/confirmar                 -> { id, ok, erro }
  POST /api/instagram/log                       -> { mensagem, nivel }

CONFIGURAÇÃO (variáveis de ambiente — exporte antes de rodar, ou configure
no boot script, igual ao bridge.mjs):
  KLIMA_API_BASE           — ex.: "https://klima-news-monitor.onrender.com"
  INSTAGRAM_BRIDGE_SECRET  — token secreto compartilhado com o Klima (tem
                              que bater com o mesmo valor configurado no
                              Render, em INSTAGRAM_BRIDGE_SECRET)
  POLL_INTERVAL_SECONDS    — opcional, default 60 (posts são raros — a
                              cada 15min no mínimo — não precisa de um
                              intervalo curto como o do WhatsApp)

Usuário/senha do Instagram NÃO ficam em variável de ambiente aqui — o bot
busca do painel ADM do Klima a cada execução (ver /api/instagram/credenciais),
pra dar pra trocar a senha sem precisar mexer em nada no celular. A SESSÃO
(cookies logados, gerada depois do primeiro login/2FA) fica só localmente,
em ./session.json — nunca sai do celular.

PRIMEIRO USO: rode este script manualmente uma vez (`python instagram_bot.py`)
com o celular em mãos — se o Instagram pedir verificação (código por
SMS/e-mail, ou aprovar no app), você precisa resolver isso na hora, na
primeira vez. Depois disso, a sessão salva evita pedir de novo (até o
Instagram decidir invalidar, o que pode acontecer de vez em quando — nesse
caso o bot avisa via Ntfy e tenta de novo sozinho a cada alguns minutos, mas
seve que ALGUÉM aprove manualmente no app do celular quando isso acontecer)."""

import json
import logging
import os
import random
import sys
import tempfile
import time
from pathlib import Path

import requests

# V.0.2.38: contorna um bug real do pydantic 1.10.x (a única versão que
# INSTALA nesse celular — é 32-bit ARM, pydantic 2+ precisa de um núcleo em
# Rust que não compila aqui, ver requirements.txt) rodando num Python novo
# demais pra ele (3.14, que o Termux já traz por padrão). O
# ValidatorGroup.check_for_unused() do pydantic 1.10.x acusa falso-positivo
# ("Validators defined with incorrect fields") em validadores que SÃO
# válidos — é só uma checagem de sanidade na hora de DEFINIR a classe, não
# muda nenhum comportamento de validação em tempo de execução, então
# desligá-la é seguro. Precisa vir ANTES de importar o instagrapi (que é
# quem de fato define as classes que disparam o bug).
try:
    import pydantic.class_validators as _pcv
    _pcv.ValidatorGroup.check_for_unused = lambda self: None
except Exception:
    pass

try:
    from instagrapi import Client
    from instagrapi.exceptions import ChallengeRequired, LoginRequired, ClientError
except ImportError:
    print("Falta instalar o instagrapi — rode: pip install -r requirements.txt")
    sys.exit(1)

HERE = Path(__file__).resolve().parent
SESSION_FILE = HERE / "session.json"
LOG_FILE = HERE / "klima_execution.log"

KLIMA_API_BASE = os.environ.get("KLIMA_API_BASE", "").rstrip("/")
BRIDGE_SECRET = os.environ.get("INSTAGRAM_BRIDGE_SECRET", "")
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))

if not KLIMA_API_BASE or not BRIDGE_SECRET:
    print("Faltam variáveis de ambiente: KLIMA_API_BASE e/ou INSTAGRAM_BRIDGE_SECRET.")
    print('Exporte as duas antes de rodar, ex.:')
    print('  export KLIMA_API_BASE="https://klima-news-monitor.onrender.com"')
    print('  export INSTAGRAM_BRIDGE_SECRET="um-token-secreto-qualquer"')
    sys.exit(1)

logging.basicConfig(
    filename=str(LOG_FILE), level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s",
)


def log(mensagem, nivel="info", tambem_no_klima=True):
    """Loga local (arquivo + console, igual ao bridge.mjs) e, opcionalmente,
    manda pro painel ADM do Klima (Sistema & Logs) — best-effort: se a
    chamada falhar, não derruba o bot por causa disso."""
    linha = f"[{nivel.upper()}] {mensagem}"
    print(linha)
    getattr(logging, nivel if nivel in ("info", "warning", "error") else "info")(mensagem)
    if tambem_no_klima:
        try:
            requests.post(
                f"{KLIMA_API_BASE}/api/instagram/log",
                json={"secret": BRIDGE_SECRET, "mensagem": mensagem, "nivel": nivel},
                timeout=10,
            )
        except Exception:
            pass  # o log local já registrou — não é crítico se o Klima não recebeu


def notificar_admin_push(topic, title, message):
    """Push via Ntfy.sh pro celular do admin — usado quando precisa de ação
    manual (ex: aprovar um desafio de segurança do Instagram)."""
    if not topic:
        log("Ntfy não configurado (tópico vazio) — pulando notificação push.", nivel="warning")
        return
    try:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers={"Title": title, "Priority": "high", "Tags": "camera,instagram"},
            timeout=10,
        )
    except Exception as exc:
        log(f"Erro ao enviar push Ntfy: {exc}", nivel="error", tambem_no_klima=False)


# ---------- Config e credenciais (buscadas do Klima) ----------

def buscar_config():
    """Usuário/senha + tópico do Ntfy, tudo vindo do painel ADM do Klima —
    nada disso precisa existir como variável de ambiente no celular, dá pra
    trocar qualquer um dos três pelo site e o bot pega na próxima consulta."""
    resp = requests.get(
        f"{KLIMA_API_BASE}/api/instagram/credenciais",
        params={"secret": BRIDGE_SECRET}, timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


# ---------- Fila: pergunta pro Klima se tem post pendente ----------

def buscar_fila():
    resp = requests.get(
        f"{KLIMA_API_BASE}/api/instagram/fila",
        params={"secret": BRIDGE_SECRET}, timeout=15,
    )
    if not resp.ok:
        detalhe = ""
        try:
            detalhe = f" — {resp.json().get('erro', '')}"
        except Exception:
            pass
        raise RuntimeError(f"Klima respondeu status {resp.status_code} ao buscar a fila{detalhe}")
    return resp.json()


def confirmar_post(post_id, ok, erro=None, media_id=None):
    try:
        requests.post(
            f"{KLIMA_API_BASE}/api/instagram/confirmar",
            json={"secret": BRIDGE_SECRET, "id": post_id, "ok": ok, "erro": erro, "media_id": media_id},
            timeout=15,
        )
    except Exception as exc:
        log(f"Falha ao confirmar resultado do post #{post_id} pro Klima: {exc}", nivel="error")


# ---------- Instagram (instagrapi) ----------

_client = None
_ntfy_topic_cache = ""


def _obter_client():
    """Sessão fica em session.json, local — só é gerada de novo (ou pedida
    por login) se não existir ou tiver expirado. Login usa usuário/senha
    vindos do Klima (ver buscar_config), nunca de variável de ambiente."""
    global _client
    if _client is not None:
        return _client

    cl = Client()
    cfg = buscar_config()
    global _ntfy_topic_cache
    _ntfy_topic_cache = cfg.get("ntfy_topic", "")

    if not cfg.get("username") or not cfg.get("password"):
        raise RuntimeError("Usuário/senha do Instagram ainda não configurados no painel ADM do Klima.")

    if SESSION_FILE.exists():
        try:
            cl.load_settings(str(SESSION_FILE))
            cl.login(cfg["username"], cfg["password"])
            cl.get_timeline_feed()  # confirma que a sessão carregada ainda é válida de verdade
            log("Sessão do Instagram carregada e validada.")
            _client = cl
            return cl
        except Exception as exc:
            log(f"Sessão salva não é mais válida ({exc}) — fazendo login novo.", nivel="warning")

    try:
        cl.login(cfg["username"], cfg["password"])
        cl.dump_settings(str(SESSION_FILE))
        log("Login novo no Instagram feito com sucesso — sessão salva.")
        _client = cl
        return cl
    except ChallengeRequired:
        notificar_admin_push(
            _ntfy_topic_cache, "Klima — ação manual necessária",
            "Ação Manual Necessária no Celular: acesse o app do Instagram pra aprovar a verificação.",
        )
        log("ChallengeRequired no login — aprove manualmente no app do Instagram no celular.",
            nivel="error")
        raise


def publicar_carrossel(post):
    cl = _obter_client()
    with tempfile.TemporaryDirectory(prefix="klima_ig_") as tmpdir:
        caminhos = []
        for i, url in enumerate(post["image_urls"][:10]):  # limite do Instagram pra carrossel
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            caminho = Path(tmpdir) / f"card_{i}.jpg"
            caminho.write_bytes(resp.content)
            caminhos.append(str(caminho))

        if not caminhos:
            raise RuntimeError("Post sem nenhuma imagem baixável.")

        # Delay humanizado antes do upload (pedido explícito — reduz o
        # padrão robótico de "responde instantâneo toda vez").
        time.sleep(random.randint(5, 12))

        if len(caminhos) == 1:
            media = cl.photo_upload(caminhos[0], caption=post["caption"])
        else:
            media = cl.album_upload(caminhos, caption=post["caption"])
        # V.0.2.33: o pk do post publicado é como o Klima liga "comentário
        # nesse post" ao link certo (ver comment_watcher.py) — devolve pra
        # quem chamou salvar no confirmar_post.
        return str(media.pk) if media and getattr(media, "pk", None) else None


# ---------- Loop principal ----------

def processar_uma_rodada():
    try:
        dados = buscar_fila()
    except Exception as exc:
        log(f"Erro ao buscar a fila no Klima: {exc}", nivel="error")
        return

    if dados.get("pausado"):
        return
    aguardar = dados.get("aguardar_segundos") or 0
    if aguardar > 0:
        return  # trava de intervalo mínimo ainda ativa — servidor já calculou, só espera o próximo poll

    posts = dados.get("posts") or []
    for post in posts:
        try:
            media_id = publicar_carrossel(post)
            confirmar_post(post["id"], True, media_id=media_id)
            log(f"✅ Post #{post['id']} publicado no Instagram (media_id={media_id}).")
        except ChallengeRequired:
            confirmar_post(post["id"], False, "ChallengeRequired — verificação manual pendente")
            global _client
            _client = None  # força tentar login de novo na próxima rodada, depois de aprovado
        except Exception as exc:
            log(f"❌ Falha ao publicar post #{post['id']}: {exc}", nivel="error")
            confirmar_post(post["id"], False, str(exc))


def main():
    log(f"Iniciando o bot do Instagram (poll a cada {POLL_INTERVAL_SECONDS}s)...")
    while True:
        try:
            processar_uma_rodada()
        except Exception as exc:
            # Nunca deixa uma exceção inesperada matar o processo — só loga
            # e tenta de novo na próxima rodada (o pm2 reinicia sozinho de
            # qualquer forma, mas isso evita reinícios desnecessários).
            log(f"Erro inesperado no loop principal: {exc}", nivel="error")
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Encerrando (Ctrl+C).")
