#!/data/data/com.termux/files/usr/bin/sh
# Roda isso no celular sempre que quiser puxar as atualizações mais recentes
# do GitHub e reiniciar os dois bridges com o código novo.
#   cd ~/klima-whatsapp-bridge && ./update.sh
set -e
cd "$(dirname "$0")"
echo "Puxando atualizações do GitHub..."
git pull
echo "Reinstalando dependências (rápido se nada mudou)..."
npm install --silent
pip install -q -r requirements.txt
echo "Reiniciando os bridges..."
pm2 restart klima-whatsapp klima-instagram
echo "Pronto. Confira com: pm2 logs"
