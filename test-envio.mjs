// Teste manual de envio — NÃO faz parte do bridge principal (bridge.mjs).
// Serve só pra isolar se o problema de entrega é:
//   (a) específico do número 5561991673965, ou
//   (b) geral (conta WhatsApp Business, versão do Baileys, etc.)
//
// Reaproveita a MESMA sessão já autenticada em auth_state/ (não precisa
// escanear QR de novo) — por isso rode isso só DEPOIS que o bridge.mjs já
// tiver conectado com sucesso pelo menos uma vez.
//
// COMO USAR (dentro da pasta termux-bridge, com o bridge.mjs PARADO —
// pm2 stop klima-whatsapp — pra não disputar a mesma sessão):
//
//   node test-envio.mjs 5511999999999 "mensagem de teste"
//
// Troque 5511999999999 por um número que você tenha CERTEZA que tem
// WhatsApp normal (de preferência já salvo nos contatos do celular do
// bridge) e que alguém consiga checar na hora se recebeu.

import makeWASocket, {
  useMultiFileAuthState,
  makeCacheableSignalKeyStore,
  fetchLatestBaileysVersion,
} from '@whiskeysockets/baileys';
import pino from 'pino';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const AUTH_DIR = path.join(__dirname, 'auth_state');

const numero = process.argv[2];
const texto = process.argv[3] || 'Mensagem de teste do Klima (test-envio.mjs)';

if (!numero) {
  console.error('Uso: node test-envio.mjs <numero-com-ddi-ddd> ["mensagem"]');
  console.error('Exemplo: node test-envio.mjs 5511999999999 "oi, teste"');
  process.exit(1);
}

const logger = pino({ level: 'warn' });

async function main() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();

  const sock = makeWASocket({
    version,
    logger,
    auth: {
      creds: state.creds,
      keys: makeCacheableSignalKeyStore(state.keys, logger),
    },
  });

  sock.ev.on('creds.update', saveCreds);

  await new Promise((resolve, reject) => {
    sock.ev.on('connection.update', async (u) => {
      const { connection, lastDisconnect } = u;
      if (connection === 'open') {
        console.log('Conectado. Verificando número no WhatsApp antes de mandar...');
        try {
          // onWhatsApp() consulta o próprio WhatsApp se o número existe/tem
          // conta ativa — é o mesmo tipo de checagem "USync" que dá erro no
          // envio direto, então isso já isola o problema sem gastar um envio.
          const resultado = await sock.onWhatsApp(numero);
          console.log('Resultado onWhatsApp():', JSON.stringify(resultado, null, 2));

          if (!resultado || resultado.length === 0 || !resultado[0]?.exists) {
            console.log('❌ O WhatsApp NÃO reconhece esse número como uma conta ativa (ou a checagem falhou). Isso explicaria o "USync...no results" — é um problema do número, não da conta Business nem do código.');
          } else {
            const jidReal = resultado[0].jid;
            console.log(`✅ Número reconhecido. JID real: ${jidReal}. Mandando mensagem de teste...`);
            await sock.sendMessage(jidReal, { text: texto });
            console.log('✅ sendMessage() não lançou erro. Confira AGORA no WhatsApp de verdade se a mensagem chegou.');
          }
        } catch (e) {
          console.error('Erro durante o teste:', e);
        } finally {
          resolve();
        }
      } else if (connection === 'close') {
        reject(new Error('Conexão fechou antes de conseguir testar: ' + (lastDisconnect?.error?.message || 'motivo desconhecido')));
      }
    });
  });

  process.exit(0);
}

main().catch((e) => {
  console.error('Erro fatal:', e);
  process.exit(1);
});
