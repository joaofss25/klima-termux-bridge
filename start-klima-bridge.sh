#!/data/data/com.termux/files/usr/bin/sh
# Início manual dos dois bridges (uso: primeira vez, ou depois de um "git
# pull" — o boot automático já faz isso sozinho, ver start-klima-bridge-boot.sh).
termux-wake-lock

cd "$(dirname "$0")" || exit 1
if [ ! -f .env ]; then
    echo "Falta o arquivo .env — copie .env.example pra .env e preencha os valores."
    exit 1
fi
set -a
. ./.env
set +a

pm2 start bridge.mjs --name klima-whatsapp
pm2 start instagram_bot.py --name klima-instagram --interpreter python3
