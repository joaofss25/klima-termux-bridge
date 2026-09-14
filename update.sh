#!/data/data/com.termux/files/usr/bin/sh
# Roda isso no celular sempre que quiser puxar as atualizações mais recentes
# do GitHub e reiniciar os bridges com o código novo.
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

# V.0.2.33: o 3º processo (DM watcher) é opcional — só sobe se
# DM_BRIDGE_SECRET estiver preenchido no .env. "pm2 restart" sozinho não
# funciona pra ligar um processo que nunca rodou antes (dá erro "process
# not found"), então tenta reiniciar e, se não existir ainda, inicia do zero.
if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi
if [ -n "$DM_BRIDGE_SECRET" ]; then
    echo "DM_BRIDGE_SECRET configurado — subindo/reiniciando klima-dm-watcher..."
    pm2 restart klima-dm-watcher 2>/dev/null || pm2 start comment_watcher.py --name klima-dm-watcher --interpreter python3
else
    echo "DM_BRIDGE_SECRET vazio no .env — klima-dm-watcher fica desligado (normal, é opcional)."
fi

echo "Pronto. Confira com: pm2 logs"
