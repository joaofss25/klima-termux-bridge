#!/data/data/com.termux/files/usr/bin/sh
# Klima — script de BOOT AUTOMÁTICO (Termux:Boot).
#
# O QUE ISSO FAZ: o Termux:Boot roda esse arquivo sozinho toda vez que o
# Android termina de ligar (sem precisar abrir o Termux na mão) — desde
# que ele esteja salvo em ~/.termux/boot/ e marcado como executável.
#
# COMO INSTALAR (rode 1x só, depois do reboot já funciona sozinho sempre):
#   mkdir -p ~/.termux/boot
#   cp start-klima-bridge-boot.sh ~/.termux/boot/
#   chmod +x ~/.termux/boot/start-klima-bridge-boot.sh
#
# V.0.2.32: os segredos NÃO ficam mais neste arquivo (que agora vai pro
# GitHub) — ficam em ".env", ao lado deste script, que é local e nunca sai
# do celular (ver .env.example pro modelo). Sobe os DOIS bridges (WhatsApp
# + Instagram) — "uma coisa só", mesma pasta, pm2 gerenciando os dois
# processos separados (um Node.js, outro Python).

# Termux:Boot roda isso bem cedo no boot do Android — às vezes antes do
# WiFi terminar de conectar. Essa espera evita tentar mandar mensagem
# (ou o pm2 tentar startar) antes da rede estar de pé.
sleep 15

# Evita o Android suspender o Termux em segundo plano.
termux-wake-lock

# Liga o SSH de novo, pra você poder continuar controlando pelo computador
# depois do reboot sem precisar destravar o celular.
sshd

cd "$HOME/klima-whatsapp-bridge" || exit 1

if [ ! -f .env ]; then
    termux-notification --title "Klima" --content "ERRO: arquivo .env não encontrado — bridges não iniciados. Copie .env.example pra .env e preencha." 2>/dev/null
    exit 1
fi
# Carrega KLIMA_API_BASE / WHATSAPP_BRIDGE_SECRET / INSTAGRAM_BRIDGE_SECRET do .env local.
set -a
. ./.env
set +a

# Remove qualquer registro antigo do pm2 pra esses apps antes de subir de
# novo — evita eles voltarem com uma variável de ambiente desatualizada
# (foi exatamente isso que causou o 403 numa rodada anterior, quando
# "pm2 restart" sozinho não pegava o export novo).
pm2 delete klima-whatsapp > /dev/null 2>&1
pm2 start bridge.mjs --name klima-whatsapp

pm2 delete klima-instagram > /dev/null 2>&1
pm2 start instagram_bot.py --name klima-instagram --interpreter python3

# Avisa na tela do celular (precisa do Termux:API, que você já tem) que
# deu certo — não depende de olhar o log pra saber que voltou sozinho.
termux-notification --title "Klima" --content "Bridges do WhatsApp e Instagram reiniciados automaticamente após o boot." 2>/dev/null
