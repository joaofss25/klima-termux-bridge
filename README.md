# Klima — bridges do Termux (WhatsApp + Instagram)

Dois processos separados que rodam no celular (via Termux + pm2), cada um falando com o painel
do Klima (Render) por polling HTTP. Ficam nesta MESMA pasta de propósito — "uma coisa só" — mas
são independentes: um é Node.js (WhatsApp, via Baileys), o outro é Python (Instagram, via
instagrapi). Um pode estar pausado/quebrado sem afetar o outro.

| Processo (pm2)     | Arquivo             | Linguagem | O que faz                                   |
|---------------------|----------------------|-----------|----------------------------------------------|
| `klima-whatsapp`    | `bridge.mjs`         | Node.js   | Manda mensagens de WhatsApp da fila do Klima |
| `klima-instagram`   | `instagram_bot.py`   | Python    | Publica o carrossel de notícias no Instagram |

## Atualizar depois (uso do dia a dia)

Este projeto vive num repositório git próprio (separado do `klima_app`) — assim como você já
faz `git push` no projeto web pro Render atualizar sozinho, aqui é o mesmo fluxo, só que ao
contrário: você edita/comita/dá push no PC, e no celular só puxa:

```
cd ~/klima-whatsapp-bridge && ./update.sh
```

Isso faz `git pull` + reinstala dependências (se mudou algo) + reinicia os dois bridges. Um
comando só, sempre que eu mandar uma atualização.

## Setup inicial (1x só, num celular novo)

1. No Termux: `pkg install git nodejs python` (se ainda não tiver).
2. Clone o repositório (troque `<url>` pela URL do repositório que foi criado no GitHub):
   ```
   git clone <url> ~/klima-whatsapp-bridge
   cd ~/klima-whatsapp-bridge
   ```
3. **Node.js**: `npm install` (usa o `package.json` já existente).
4. **Python** (2 passos — ver comentário no topo do `requirements.txt` pra entender por quê):
   ```
   pip install -r requirements.txt
   pip install instagrapi==1.16.42 --no-deps
   ```
5. **Crie o `.env`** (esse arquivo NUNCA vai pro GitHub — fica só neste celular):
   ```
   cp .env.example .env
   nano .env
   ```
   Preencha `WHATSAPP_BRIDGE_SECRET` e `INSTAGRAM_BRIDGE_SECRET` — qualquer string longa e
   aleatória, só precisam bater EXATAMENTE com o que está configurado no Render (Environment
   Variables do serviço `klima-web`).
6. **Primeiro login do WhatsApp**: rode `pm2 start bridge.mjs --name klima-whatsapp` uma vez à
   mão e escaneie o QR Code que aparece no terminal (Aparelhos conectados > Conectar um aparelho).
7. **Primeiro login do Instagram**: antes de tudo, cadastre o usuário/senha do Instagram no
   painel ADM do Klima (Configurações > 📸 Postagens > Configuração) — o bot busca a senha de lá,
   não daqui. Depois, rode `python instagram_bot.py` uma vez à mão, com o celular por perto: se o
   Instagram pedir verificação (código por SMS/e-mail, ou aprovação pelo app), resolva na hora.
   Depois desse primeiro login, a sessão fica salva em `session.json` (local, nunca sai do
   celular) e os próximos boots não pedem de novo — só se o Instagram decidir invalidar a sessão
   depois de um tempo (o bot avisa via Ntfy quando isso acontece).
8. **Ativar no boot automático** (pra sobreviver a reiniciar o celular):
   ```
   mkdir -p ~/.termux/boot
   cp start-klima-bridge-boot.sh ~/.termux/boot/
   chmod +x ~/.termux/boot/start-klima-bridge-boot.sh
   ```
9. Instale o app **Termux:Boot** (fora da Play Store recomendada, mesma fonte do Termux — F-Droid
   ou GitHub) e o app **Ntfy** (Play Store), se ainda não tiver — o Ntfy é pra receber os alertas
   de "ação manual necessária" quando o Instagram pedir verificação.

## Dia a dia

- **Pausar/retomar, trocar senha do Instagram, ver a fila**: tudo pelo painel ADM do Klima no
  navegador (Configurações > 📸 Postagens e > 💬 Notificações) — não precisa mexer no celular pra
  isso.
- **Ver os logs de verdade** (o que o bot está tentando fazer agora): `pm2 logs klima-instagram`
  ou `pm2 logs klima-whatsapp` no Termux, ou o arquivo `klima_execution.log` nesta pasta. Eventos
  importantes também aparecem em Configurações > 🧾 Sistema & Logs no site, prefixados com
  `[Instagram bot]`.
- **Reiniciar manualmente**: `pm2 restart klima-instagram` (ou `klima-whatsapp`).
- **Bateria**: dê permissão de "sem restrição"/"ignorar otimização de bateria" pro Termux nas
  configurações do Android — sem isso, o sistema mata os processos em segundo plano e os bridges
  param de responder sozinhos, sem aviso.
