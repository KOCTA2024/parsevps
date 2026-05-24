'use strict';

/**
 * worker.js
 * BullMQ worker that processes matured "analyse" jobs from the match-analysis queue.
 *
 * For each job it runs the two-step chain:
 *   1. node src/match_h2h_export.js --matchId <ID>
 *   2. python3 src/math_script.py data/<sanitized_filename>.json   (only if step 1 exits 0)
 *
 * Usage:  node src/worker.js
 *         pm2 start src/worker.js --name match-worker
 */
import path from 'path';
import fs from 'fs';
import { execFile } from 'child_process';
import { Worker, MetricsTime } from 'bullmq';

// Если в коде ниже дальше использовался __dirname, 
// не забудь добавить две эти строчки для его воссоздания:
import { fileURLToPath } from 'url';
const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ─── Config ──────────────────────────────────────────────────────────────────

const REDIS_CONFIG = {
  host: process.env.REDIS_HOST || '127.0.0.1',
  port: Number(process.env.REDIS_PORT) || 6379,
  password: process.env.REDIS_PASSWORD || undefined,
};

const QUEUE_NAME  = 'match-analysis';
const APP_ROOT    = path.resolve(__dirname, '..');          // ~/backup_app/app
const NODE_BIN    = process.execPath;                       // same Node version as the worker
const PYTHON_BIN  = process.env.PYTHON_BIN || 'python3';

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Promisified execFile wrapper.
 * Resolves with { stdout, stderr, code }.
 * Rejects only on spawn / system-level errors (not on non-zero exit).
 */
function run(bin, args, opts = {}) {
  return new Promise((resolve, reject) => {
    const child = execFile(bin, args, { cwd: APP_ROOT, ...opts }, (err, stdout, stderr) => {
      if (err && err.code === undefined) {
        // Spawn error (binary not found, permission denied, etc.)
        reject(err);
        return;
      }
      resolve({
        stdout: stdout.trim(),
        stderr: stderr.trim(),
        code:   err ? err.code : 0,
      });
    });

    // Stream output to the worker's stdout/stderr in real time (useful with pm2 logs)
    child.stdout?.pipe(process.stdout, { end: false });
    child.stderr?.pipe(process.stderr, { end: false });
  });
}

/**
 * Log with job context prefix, e.g.  [worker][job:42] message
 */
function log(jobId, level, ...args) {
  const prefix = `[worker][job:${jobId}]`;
  if (level === 'error') {
    console.error(prefix, ...args);
  } else {
    console.log(prefix, ...args);
  }
}

// ─── Job Processor ───────────────────────────────────────────────────────────

async function processJob(job) {
  const { matchId, home, away, homeSlug, awaySlug, league, dataFilename, lineFilename, fireAt } = job.data;
  const jid = job.id ?? matchId;

  log(jid, 'info',
    `Starting analysis — ${home} vs ${away} [${league}]` +
    ` (scheduled fire: ${fireAt})`
  );

  // ── Step 1: Node parser ──────────────────────────────────────────────────
  const parserScript = path.join(APP_ROOT, 'src', 'match_h2h_export.js');

  log(jid, 'info', `Step 1 → node ${parserScript} --matchId ${matchId} --home ${homeSlug} --away ${awaySlug}`);

  await job.updateProgress(10);

  const parserResult = await run(NODE_BIN, [
    parserScript,
    '--matchId', matchId,
    '--home',    homeSlug,
    '--away',    awaySlug,
  ]);

  log(jid, 'info',
    `Step 1 exited with code ${parserResult.code}` +
    (parserResult.stderr ? `\nSTDERR: ${parserResult.stderr}` : '')
  );

  if (parserResult.code !== 0) {
    throw new Error(
      `Parser (match_h2h_export.js) failed with exit code ${parserResult.code}. ` +
      `STDERR: ${parserResult.stderr || '(none)'}`
    );
  }

  await job.updateProgress(50);

  // ── Step 2: Python calculator ────────────────────────────────────────────
  const dataFilePath = path.join(APP_ROOT, 'data', dataFilename);
  const lineFilePath = path.join(APP_ROOT, 'data', lineFilename);
  const calcScript   = path.join(APP_ROOT, 'src', 'math_script.py');

  log(jid, 'info', `Step 2 → ${PYTHON_BIN} ${calcScript} ${dataFilePath} ${lineFilePath}`);

  const calcResult = await run(PYTHON_BIN, [calcScript, dataFilePath, lineFilePath]);

  log(jid, 'info',
    `Step 2 exited with code ${calcResult.code}` +
    (calcResult.stderr ? `\nSTDERR: ${calcResult.stderr}` : '')
  );

  if (calcResult.code !== 0) {
    throw new Error(
      `Calculator (math_script.py) failed with exit code ${calcResult.code}. ` +
      `STDERR: ${calcResult.stderr || '(none)'}`
    );
  }

  // ── Cleanup: remove per-match data files to prevent unbounded folder growth ──
  for (const filePath of [dataFilePath, lineFilePath]) {
    try {
      fs.unlinkSync(filePath);
      log(jid, 'info', `Cleaned up ${filePath}`);
    } catch (e) {
      log(jid, 'info', `Could not delete ${filePath}: ${e.message}`);
    }
  }

  await job.updateProgress(100);

  log(jid, 'info', `✓ Analysis chain completed successfully for match ${matchId}`);

  return {
    matchId,
    parserExitCode: parserResult.code,
    calcExitCode:   calcResult.code,
    completedAt:    new Date().toISOString(),
  };
}

// ─── Worker Setup ─────────────────────────────────────────────────────────────

const worker = new Worker(QUEUE_NAME, processJob, {
  connection: REDIS_CONFIG,
  concurrency: Number(process.env.WORKER_CONCURRENCY) || 2,   // process 2 matches at once max
  metrics: {
    maxDataPoints: MetricsTime.ONE_WEEK,
  },
});

// ─── Event Listeners ─────────────────────────────────────────────────────────

worker.on('completed', (job, result) => {
  console.log(
    `[worker] ✓ Job ${job.id} completed.`,
    `Match: ${job.data.matchId} | Parser: ${result.parserExitCode} | Calc: ${result.calcExitCode}`
  );
});

worker.on('failed', (job, err) => {
  const attempts = job?.attemptsMade ?? '?';
  const max      = job?.opts?.attempts ?? '?';
  console.error(
    `[worker] ✗ Job ${job?.id} failed (attempt ${attempts}/${max}):`,
    err.message
  );
});

worker.on('error', err => {
  // Connection errors, serialisation problems, etc.
  console.error('[worker] Worker-level error:', err);
});

worker.on('stalled', jobId => {
  console.warn(`[worker] Job ${jobId} stalled — will be retried.`);
});

// ─── Graceful Shutdown ────────────────────────────────────────────────────────

async function shutdown(signal) {
  console.log(`\n[worker] ${signal} received — draining queue and shutting down…`);
  await worker.close();
  console.log('[worker] Shutdown complete.');
  process.exit(0);
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT',  () => shutdown('SIGINT'));

console.log(
  `[worker] Started. Listening on queue "${QUEUE_NAME}" | Redis ${REDIS_CONFIG.host}:${REDIS_CONFIG.port}` +
  ` | Concurrency: ${worker.opts.concurrency}`
);