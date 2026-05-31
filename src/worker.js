'use strict';

/**
 * worker.js  (з Step 3 — OpenAI аналіз)
 *
 * Ланцюжок:
 *   1. node src/match_h2h_export.js  — парсер h2h
 *   2. python3 src/math_script.py    — математичний розрахунок
 *   3. openai_analyst.analyseMatch() — AI-аналіз за Basketball Master v3.1
 */
import path from 'path';
import fs from 'fs';
import { execFile } from 'child_process';
import { Worker, MetricsTime } from 'bullmq';
import { fileURLToPath } from 'url';

// ─── Step 3: OpenAI analyst ───────────────────────────────────────────────────
import { analyseMatch, setNotifier } from './openai_analyst.js';

// Щоб підключити Telegram — розкоментуй і реалізуй notifiers/telegram.js:
// import { sendTelegram } from './notifiers/telegram.js';
// setNotifier(sendTelegram);

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

  // ── Step 3: OpenAI analysis ───────────────────────────────────────────────
  log(jid, 'info', `Step 3 → OpenAI analysis (model: ${process.env.OPENAI_MODEL || 'gpt-4o'})`);

  let aiResult;
  try {
    aiResult = await analyseMatch(job.data, dataFilePath, lineFilePath);
    log(jid, 'info',
      `Step 3 completed — verdict: ${aiResult.verdict}` +
      (aiResult.recommendations?.length
        ? ` | ${aiResult.recommendations.length} recommendation(s)`
        : '')
    );
  } catch (err) {
    // AI failure is non-fatal: log it, but don't fail the whole job.
    // The data files were already produced — analysis can be re-run manually.
    log(jid, 'error', `Step 3 failed (non-fatal): ${err.message}`);
    aiResult = { verdict: 'ERROR', error: err.message };
  }
  await job.updateProgress(90);

  // ── Cleanup: remove per-match data files ─────────────────────────────────
  // NOTE: analysis_<matchId>.json is kept — it's the final output.
  for (const filePath of [dataFilePath, lineFilePath]) {
    try {
      fs.unlinkSync(filePath);
      log(jid, 'info', `Cleaned up ${filePath}`);
    } catch (e) {
      log(jid, 'info', `Could not delete ${filePath}: ${e.message}`);
    }
  }

  await job.updateProgress(100);
  log(jid, 'info', `✓ Full chain completed for match ${matchId}`);

  return {
    matchId,
    parserExitCode: parserResult.code,
    calcExitCode:   calcResult.code,
    aiVerdict:      aiResult.verdict,
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
    `Match: ${result.matchId} | AI verdict: ${result.aiVerdict}`
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