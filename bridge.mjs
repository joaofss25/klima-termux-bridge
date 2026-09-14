// Klima WhatsApp Bridge (V.0.1.86+) — roda no Termux, no celular dedicado.
//
// O QUE ISSO FAZ: conecta no WhatsApp via Baileys (protocolo do WhatsApp
// Web/multi-dispositivo) e fica perguntando pro Klima (Render), de tempos
// em tempos, se tem mensagem pendente pra mandar — se tiver, manda e avisa
// de volta que enviou.
//
// ⚠️ SÓ A PARTE DO CELULAR ESTÁ PRONTA AQUI. O lado do Klima (as duas rotas
// HTTP que esse script chama, GET /api/whatsapp/fila e POST
// /api/whatsapp/confirmar) AINDA NÃO EXISTE em app.py — precisa ser
// implementado depois, com exatamente o contrato descrito nos comentários
// de buscarPendentes()/confirmarEnvio() abaixo, protegido por um token
// secreto compartilhado (mesmo espírito do UPTIME_PING_SECRET já usado
// no projeto pro ping automático).
//
// Arquivo é ".mjs" de propósito (não ".js") — assim o Node trata como
// ES Module (permite "import") sem precisar editar o package.json.
//
// CONFIGURAÇÃO (variáveis de ambiente — exporte antes de rodar, ou
// configure no ecosystem do pm2 depois):
//   KLIMA_API_BASE         — ex.: "https://klima-news-monitor.onrender.com"
//   WHATSAPP_BRIDGE_SECRET — token secreto compartilhado com o Klima
//   POLL_INTERVAL_MS       — opcional, default 15000 (15s) entre consultas
//
// Ainda não testado rodando de verdade (sem Node disponível neste ambiente
// de desenvolvimento) — rode no Termux e me mande qualquer erro que
// aparecer, corrijo em cima do erro real.

import makeWASocket, {
  useMultiFileAuthState,
  makeCacheableSignalKeyStore,
  fetchLatestBaileysVersion,
  DisconnectReason,
} from '@whiskeysockets/baileys';
import { Boom } from '@hapi/boom';
import pino from 'pino';
import qrcode from 'qrcode-terminal';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const KLIMA_API_BASE = process.env.KLIMA_API_BASE;
const BRIDGE_SECRET = process.env.WHATSAPP_BRIDGE_SECRET;
const POLL_INTERVAL_MS = parseInt(process.env.POLL_INTERVAL_MS || '15000', 10);
const AUTH_DIR = path.join(__dirname, 'auth_state');
const LOG_FILE = path.join(__dirname, 'bridge.log');

if (!KLIMA_API_BASE || !BRIDGE_SECRET) {
  console.error('Faltam variáveis de ambiente: KLIMA_API_BASE e/ou WHATSAPP_BRIDGE_SECRET.');
  console.error('Exporte as duas antes de rodar, ex.:');
  console.error('  export KLIMA_API_BASE="https://klima-news-monitor.onrender.com"');
  console.error('  export WHATSAPP_BRIDGE_SECRET="um-token-secreto-qualquer"');
  process.exit(1);
}

const logger = pino({ level: process.env.LOG_LEVEL || 'warn' });

function log(msg) {
  const linha = `[${new Date().toISOString()}] ${msg}`;
  console.log(linha);
  try {
    fs.appendFileSync(LOG_FILE, linha + '\n');
  } catch (e) {
    // Se não der pra escrever o log em arquivo, não trava o bridge por
    // causa disso — só perde o registro em disco, o console continua.
  }
}

let sockAtual = null;
let conectado = false;

// V.0.1.91: tirei o baileys-antiban temporariamente — ele estava quebrando
// TODO envio com "Cannot read properties of undefined (reading
// 'circuitBreaker')", um erro interno da própria biblioteca que não achei
// documentado em lugar nenhum (parece bug/incompatibilidade de versão dela,
// não do nosso código). Em vez de ficar travado nisso, uso o sock cru do
// Baileys direto e um intervalo manual simples entre mensagens (função
// esperarUmPouco abaixo) como proteção básica — dá pra reavaliar o
// baileys-antiban depois, com mais calma, sem bloquear o envio agora.
function esperarUmPouco() {
  const ms = 2000 + Math.floor(Math.random() * 3000); // 2-5s, com variação
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ---------- Conexão com o WhatsApp (Baileys) ----------

async function iniciarConexao() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();

  const sock = makeWASocket({
    version,
    logger,
    auth: {
      creds: state.creds,
      keys: makeCacheableSignalKeyStore(state.keys, logger),
    },
    // "printQRInTerminal" foi descontinuado nessa versão do Baileys — agora
    // o QR chega só como dado no evento connection.update (campo "qr"), e
    // quem chama é que decide como mostrar. Desenhamos com qrcode-terminal
    // manualmente lá embaixo, dentro do sock.ev.process().
  });

  sockAtual = sock;

  sock.ev.process(async (events) => {
    if (events['connection.update']) {
      const { connection, lastDisconnect, qr } = events['connection.update'];
      if (qr) {
        log('QR code novo gerado — escaneie pelo WhatsApp do celular (Aparelhos conectados > Conectar um aparelho):');
        qrcode.generate(qr, { small: true }); // desenha o QR em ASCII direto no terminal
      }
      if (connection === 'open') {
        conectado = true;
        log('✅ Conectado ao WhatsApp.');
      } else if (connection === 'close') {
        conectado = false;
        const motivoStatus = (lastDisconnect?.error instanceof Boom)
          ? lastDisconnect.error.output?.statusCode
          : undefined;
        const deveReconectar = motivoStatus !== DisconnectReason.loggedOut;
        log(`Conexão fechada (motivo: ${lastDisconnect?.error?.message || 'desconhecido'}). Reconectar: ${deveReconectar}`);
        if (deveReconectar) {
          setTimeout(iniciarConexao, 5000);
        } else {
          log('⚠️ Sessão desconectada de vez (logout) — apague a pasta auth_state/ e escaneie o QR de novo.');
        }
      }
    }
    if (events['creds.update']) {
      await saveCreds();
    }
  });
}

// ---------- Fila: pergunta pro Klima se tem mensagem pendente ----------
//
// Contrato esperado do lado do Klima (ainda não implementado em app.py):
//
// GET /api/whatsapp/fila?secret=...
//   -> 200 { "mensagens": [ { "id": 123, "phone": "5511999999999",
//                             "message": "texto..." }, ... ] }
//
// POST /api/whatsapp/confirmar
//   body: { "secret": "...", "id": 123, "ok": true, "erro": null }
//   -> 200 { "ok": true }

async function buscarPendentes() {
  const url = `${KLIMA_API_BASE}/api/whatsapp/fila?secret=${encodeURIComponent(BRIDGE_SECRET)}`;
  const resp = await fetch(url, { method: 'GET' });
  if (!resp.ok) {
    // V.0.1.90: lê o corpo da resposta (o Klima manda o motivo exato em
    // "erro") em vez de só mostrar o número do status — bem mais fácil de
    // diagnosticar direto pelo log do bridge, sem precisar adivinhar.
    let detalhe = '';
    try {
      const corpo = await resp.json();
      detalhe = corpo && corpo.erro ? ` — ${corpo.erro}` : '';
    } catch (e) { /* corpo não era JSON, segue sem detalhe extra */ }
    throw new Error(`Klima respondeu status ${resp.status} ao buscar a fila${detalhe}`);
  }
  const dados = await resp.json();
  return dados.mensagens || [];
}

async function confirmarEnvio(id, ok, erro) {
  const url = `${KLIMA_API_BASE}/api/whatsapp/confirmar`;
  await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ secret: BRIDGE_SECRET, id, ok, erro: erro || null }),
  });
}

// V.0.1.92: trava simples pra nunca ter duas rodadas de processarFila()
// sobrepostas — o setInterval abaixo dispara a cada POLL_INTERVAL_MS
// (15s por padrão) sem esperar a rodada anterior terminar; se processar
// a fila demorar mais que isso (fácil de acontecer com o intervalo de
// 2-5s entre mensagens + o tempo de envio de cada uma), uma rodada nova
// começava buscando os MESMOS itens ainda "pendentes" e mandava tudo de
// novo — era isso que causava as mensagens duplicadas.
let processandoFila = false;

async function processarFila() {
  if (processandoFila) return; // já tem uma rodada em andamento, não sobrepõe
  if (!conectado || !sockAtual) return;
  processandoFila = true;

  try {
    let pendentes;
    try {
      pendentes = await buscarPendentes();
    } catch (e) {
      log(`Erro ao buscar fila no Klima: ${e.message}`);
      return;
    }

    let primeira = true;
    for (const msg of pendentes) {
      if (!primeira) await esperarUmPouco(); // intervalo entre mensagens, não dispara tudo de uma vez
      primeira = false;
      try {
        // V.0.1.93: antes mandávamos direto pro JID montado na mão
        // (`${msg.phone}@s.whatsapp.net`) — funcionava sem lançar erro
        // (sendMessage "aceitava"), mas a mensagem não chegava de verdade,
        // com "USync fetch yielded no results for pending PNs" no log.
        // Resolver o número via onWhatsApp() ANTES de mandar (mesma
        // verificação que o WhatsApp faz por trás, mas explícita e com
        // resultado conferido aqui) corrigiu isso num teste manual real —
        // por isso agora fazemos essa etapa sempre, e só mandamos se o
        // número for reconhecido como conta ativa.
        const resolvido = await sockAtual.onWhatsApp(msg.phone);
        if (!resolvido || resolvido.length === 0 || !resolvido[0]?.exists) {
          throw new Error(`Número ${msg.phone} não foi reconhecido como conta WhatsApp ativa`);
        }
        const jid = resolvido[0].jid;
        await sockAtual.sendMessage(jid, { text: msg.message });
        await confirmarEnvio(msg.id, true);
        log(`✅ Mensagem ${msg.id} enviada pra ${msg.phone}.`);
      } catch (e) {
        log(`❌ Falha ao enviar mensagem ${msg.id}: ${e.message}`);
        try {
          await confirmarEnvio(msg.id, false, e.message);
        } catch (e2) {
          log(`(e também falhou ao avisar o Klima do erro: ${e2.message})`);
        }
      }
    }
  } finally {
    processandoFila = false;
  }
}

// ---------- Início ----------

log('Iniciando o bridge do WhatsApp...');
iniciarConexao().catch((e) => {
  log(`Erro fatal ao iniciar a conexão: ${e.message}`);
  process.exit(1);
});
setInterval(processarFila, POLL_INTERVAL_MS);

process.on('SIGTERM', () => { log('Encerrando (SIGTERM).'); process.exit(0); });
process.on('SIGINT', () => { log('Encerrando (SIGINT).'); process.exit(0); });
