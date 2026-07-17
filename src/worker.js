'use strict';

/**
 * worker.js  (з Step 3 — OpenAI аналіз)
 *
 * Ланцюжок:
 *   1. node src/match_h2h_export.js         — парсер h2h
 *   2. python3 src/math_script.py           — математичний розрахунок
 *   3. python3 src/super_basket_vps_system.py run — розрахунок P_final,
 *      gates (stat/conflict/router/Team-IT/Q4), GPT-review сигналу,
 *      відправка PLAY/RISK в Telegram, запис у SQLite.
 */
import path from 'path';
import { execFile } from 'child_process';
import { Worker, MetricsTime } from 'bullmq';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ─── Config ──────────────────────────────────────────────────────────────────

const REDIS_CONFIG = {
  host:     process.env.REDIS_HOST     || '127.0.0.1',
  port:     Number(process.env.REDIS_PORT) || 6379,
  password: process.env.REDIS_PASSWORD || undefined,
};

const QUEUE_NAME  = 'match-analysis';
const APP_ROOT    = path.resolve(__dirname, '..');
const NODE_BIN    = process.execPath;
const PYTHON_BIN  = process.env.PYTHON_BIN || 'python3';

// super_basket_vps_system.py: путь к SQLite для сигналов/калибровки.
// Лежит в state-volume, чтобы переживать пересоздание контейнера.
const SUPER_BASKET_DB = process.env.SUPER_BASKET_DB
  || path.join(APP_ROOT, 'state', 'super_basket.sqlite3');

// ─── Helpers ─────────────────────────────────────────────────────────────────

function run(bin, args, opts = {}) {
  return new Promise((resolve, reject) => {
    const child = execFile(bin, args, { cwd: APP_ROOT, ...opts }, (err, stdout, stderr) => {
      if (err && err.code === undefined) { reject(err); return; }
      resolve({ stdout: stdout.trim(), stderr: stderr.trim(), code: err ? err.code : 0 });
    });
    child.stdout?.pipe(process.stdout, { end: false });
    child.stderr?.pipe(process.stderr, { end: false });
  });
}

function log(jobId, level, ...args) {
  const prefix = `[worker][job:${jobId}]`;
  level === 'error' ? console.error(prefix, ...args) : console.log(prefix, ...args);
}

// ─── Job Processor ───────────────────────────────────────────────────────────

async function processJob(job) {
  const {
    matchId, home, away, homeSlug, awaySlug,
    league, dataFilename, lineFilename, fireAt,
  } = job.data;
  const jid = job.id ?? matchId;

  log(jid, 'info', `Starting analysis — ${home} vs ${away} [${league}] (scheduled fire: ${fireAt})`);

  // ── Step 1: Node parser ───────────────────────────────────────────────────
  const parserScript = path.join(APP_ROOT, 'src', 'match_h2h_export.js');
  log(jid, 'info', `Step 1 → node ${parserScript} --matchId ${matchId} --home ${homeSlug} --away ${awaySlug}`);
  await job.updateProgress(10);

  const parserResult = await run(NODE_BIN, [
    parserScript, '--matchId', matchId, '--home', homeSlug, '--away', awaySlug,
  ]);

  log(jid, 'info', `Step 1 exited ${parserResult.code}${parserResult.stderr ? '\n' + parserResult.stderr : ''}`);
  if (parserResult.code !== 0) {
    throw new Error(`Parser failed (exit ${parserResult.code}): ${parserResult.stderr || '(none)'}`);
  }
  await job.updateProgress(40);

  // ── Step 2: Python calculator ─────────────────────────────────────────────
  const dataFilePath = path.join(APP_ROOT, 'data', dataFilename);
  const lineFilePath = path.join(APP_ROOT, 'data', lineFilename);
  const calcScript   = path.join(APP_ROOT, 'src', 'math_script.py');

  log(jid, 'info', `Step 2 → ${PYTHON_BIN} ${calcScript} ${dataFilePath} ${lineFilePath}`);

  const calcResult = await run(PYTHON_BIN, [calcScript, dataFilePath, lineFilePath]);
  log(jid, 'info', `Step 2 exited ${calcResult.code}${calcResult.stderr ? '\n' + calcResult.stderr : ''}`);
  if (calcResult.code !== 0) {
    throw new Error(`Calculator failed (exit ${calcResult.code}): ${calcResult.stderr || '(none)'}`);
  }
  await job.updateProgress(70);

  // ── Step 3: super_basket_vps_system.py ────────────────────────────────────
  // dataFilePath к этому моменту уже содержит merged JSON (h2h + lines +
  // raw_data + team_relative_stat_zones) — это ровно то, что нужно скрипту
  // как --match. Все gates (stat/conflict/router/Team-IT/Q4), GPT-review
  // сигнала и отправка в Telegram теперь внутри этого скрипта.
  const superBasketScript = path.join(APP_ROOT, 'src', 'super_basket_vps_system.py');
  log(jid, 'info', `Step 3 → ${PYTHON_BIN} ${superBasketScript} run --match ${dataFilePath}`);

  const superBasketResult = await run(PYTHON_BIN, [
    superBasketScript, 'run',
    '--match', dataFilePath,
    '--db', SUPER_BASKET_DB,
  ]);
  log(jid, 'info', `Step 3 exited ${superBasketResult.code}${superBasketResult.stderr ? '\n' + superBasketResult.stderr : ''}`);
  await job.updateProgress(90);

  let summary = { output_status: 'ERROR' };
  if (superBasketResult.code === 0) {
    try {
      summary = JSON.parse(superBasketResult.stdout);
      log(jid, 'info',
        `Step 3 completed — decision: ${summary.decision?.action}/${summary.decision?.status}` +
        ` | gpt: ${summary.gpt_status} | telegram: ${summary.telegram_status}`
      );
    } catch (e) {
      // Скрипт отработал (exit 0), но stdout не распарсился — не фейлим job,
      // данные и sqlite-запись уже сделаны, разбираемся по логам.
      log(jid, 'error', `Step 3 stdout parse failed (non-fatal): ${e.message}`);
    }
  } else {
    // Как и раньше с AI-шагом: ошибка Step 3 не фейлит весь job — файлы
    // уже посчитаны, матч можно прогнать вручную: python3 src/super_basket_vps_system.py run --match ...
    log(jid, 'error', `Step 3 failed (non-fatal, exit ${superBasketResult.code})`);
  }

  await job.updateProgress(100);
  log(jid, 'info', `✓ Full chain completed for match ${matchId}`);

  return {
    matchId,
    parserExitCode: parserResult.code,
    calcExitCode:   calcResult.code,
    superBasketExitCode: superBasketResult.code,
    decision:       summary.decision ?? null,
    outputStatus:   summary.output_status,
    completedAt:    new Date().toISOString(),
  };
}

// ─── Worker Setup ─────────────────────────────────────────────────────────────

const worker = new Worker(QUEUE_NAME, processJob, {
  connection:  REDIS_CONFIG,
  concurrency: Number(process.env.WORKER_CONCURRENCY) || 2,
  metrics:     { maxDataPoints: MetricsTime.ONE_WEEK },
});

worker.on('completed', (job, result) => {
  console.log(
    `[worker] ✓ Job ${job.id} completed.`,
    `Match: ${result.matchId} | decision: ${result.decision?.action ?? 'n/a'}/${result.decision?.status ?? 'n/a'}`
  );
});

worker.on('failed', (job, err) => {
  console.error(
    `[worker] ✗ Job ${job?.id} failed (attempt ${job?.attemptsMade}/${job?.opts?.attempts}):`,
    err.message
  );
});

worker.on('error',   (err)   => console.error('[worker] Worker error:', err));
worker.on('stalled', (jobId) => console.warn(`[worker] Job ${jobId} stalled — retrying.`));

async function shutdown(signal) {
  console.log(`\n[worker] ${signal} — draining and shutting down…`);
  await worker.close();
  console.log('[worker] Shutdown complete.');
  process.exit(0);
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT',  () => shutdown('SIGINT'));

console.log(
  `[worker] Started. Queue "${QUEUE_NAME}" | Redis ${REDIS_CONFIG.host}:${REDIS_CONFIG.port}` +
  ` | Concurrency: ${worker.opts.concurrency}`
);