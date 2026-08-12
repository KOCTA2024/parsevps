'use strict';

/**
 * worker.js  (Step 3 — детермінований аналіз без OpenAI)
 *
 * Ланцюжок:
 *   1. node src/match_h2h_export.js         — парсер h2h
 *   2. python3 src/math_script.py           — математичний розрахунок
 *   3. python3 src/super_basket_v15_3_hybrid_pro.py run — головний v15.3 радник.
 *      Він динамічно завантажує basketball_score_predictor_v4.py, v14-router
 *      та production calibration, формує одну рекомендацію, пише JSON/SQLite
 *      і за прапором --telegram відправляє повідомлення.
 *
 * Checkpoint #6 після завершення матчу виконує лише кроки 1–2 і зберігає
 * *_result_checkpoint6.json. Крок 3, Telegram і SQLite для нього вимкнені.
 */
import path from 'path';
import fs from 'fs';
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
const ENABLE_FIVE_CHECKPOINTS = /^(1|true|yes|on)$/i.test(
  String(process.env.WORKER_ENABLE_FIVE_CHECKPOINTS || 'false')
);

// super_basket_vps_system.py: путь к SQLite для сигналов/калибровки.
// Лежит в state-volume, чтобы переживать пересоздание контейнера.
const SUPER_BASKET_DB = process.env.SUPER_BASKET_DB
  || path.join(APP_ROOT, 'state', 'super_basket_v15_3.sqlite3');
const MATCH_FILES_DIR = process.env.MATCH_FILES_DIR
  || path.join(APP_ROOT, 'state', 'match_files');

// v15.3 is the only executable entry point for the advisory step.
// The three dependency paths are passed explicitly so deployment does not rely
// on cwd-based sibling discovery. Filenames with spaces are safe with execFile.
const SUPER_BASKET_V15_SCRIPT = process.env.SUPER_BASKET_V15_SCRIPT
  || path.join(APP_ROOT, 'src', 'super_basket_v15_3_hybrid_pro.py');
const SCORE_MODEL_SCRIPT = process.env.SUPER_BASKET_SCORE_MODEL
  || path.join(APP_ROOT, 'src', 'basketball_score_predictor_v4.py');
const LEGACY_ADVISOR_SCRIPT = process.env.SUPER_BASKET_LEGACY_ADVISOR
  || path.join(APP_ROOT, 'src', 'super_basket_vps_system_FINAL_v14_2_ALWAYS_TELEGRAM_STAGE_ROUTER 2.py');
const CALIBRATION_FILE = process.env.SUPER_BASKET_CALIBRATION
  || path.join(APP_ROOT, 'src', 'v15_2_calibration_production_485.json');
const V15_SIMULATIONS = Number(process.env.SUPER_BASKET_SIMULATIONS) || 12000;

// ─── Helpers ─────────────────────────────────────────────────────────────────

function pipeWithPrefix(stream, target, prefix) {
  if (!stream) return;
  let buf = '';
  stream.on('data', (chunk) => {
    buf += chunk.toString();
    const lines = buf.split('\n');
    buf = lines.pop(); // keep incomplete trailing line in buffer
    for (const line of lines) target.write(`${prefix} ${line}\n`);
  });
  stream.on('end', () => {
    if (buf) target.write(`${prefix} ${buf}\n`);
  });
}

function run(bin, args, opts = {}) {
  const { jobId, ...execOpts } = opts;
  return new Promise((resolve, reject) => {
    const child = execFile(bin, args, { cwd: APP_ROOT, ...execOpts }, (err, stdout, stderr) => {
      if (err && err.code === undefined) { reject(err); return; }
      resolve({ stdout: stdout.trim(), stderr: stderr.trim(), code: err ? err.code : 0 });
    });
    const prefix = jobId ? `[worker][job:${jobId}]` : '[worker]';
    pipeWithPrefix(child.stdout, process.stdout, prefix);
    pipeWithPrefix(child.stderr, process.stderr, prefix);
  });
}

function log(jobId, level, ...args) {
  const prefix = `[worker][job:${jobId}]`;
  level === 'error' ? console.error(prefix, ...args) : console.log(prefix, ...args);
}

/**
 * Compatibility router while the calculator accepts only its historical
 * three checkpoints: 1=Q2 snapshot, 2=HT, 3=Q4 snapshot.
 *
 * New stage-monitor numbering is 1=pre-match, 2=Q1, 3=Q2, 4=HT, 5=Q4.
 * With WORKER_ENABLE_FIVE_CHECKPOINTS=false, #1/#2 are acknowledged as
 * skipped and #3/#4/#5 are translated to the calculator's old #1/#2/#3.
 * Old jobs without stageCheckpoint remain backward-compatible.
 */
function resolveWorkerCheckpoint(jobData, fiveCheckpointMode = ENABLE_FIVE_CHECKPOINTS) {
  const hasNewStageMarker = jobData.stageCheckpoint !== undefined &&
    jobData.stageCheckpoint !== null;
  const stageCheckpoint = Number(hasNewStageMarker ? jobData.stageCheckpoint : jobData.checkpoint) || 0;

  if (fiveCheckpointMode) {
    return {
      accepted: stageCheckpoint >= 1 && stageCheckpoint <= 5,
      stageCheckpoint,
      calculatorCheckpoint: stageCheckpoint,
    };
  }

  if (!hasNewStageMarker) {
    return {
      accepted: stageCheckpoint >= 1 && stageCheckpoint <= 3,
      stageCheckpoint,
      calculatorCheckpoint: stageCheckpoint,
    };
  }

  const calculatorCheckpoint = ({ 3: 1, 4: 2, 5: 3 })[stageCheckpoint] || 0;
  return {
    accepted: calculatorCheckpoint > 0,
    stageCheckpoint,
    calculatorCheckpoint,
  };
}

// ─── Job Processor ───────────────────────────────────────────────────────────

async function processJob(job) {
  const {
    matchId, home, away, homeSlug, awaySlug,
    league, dataFilename, lineFilename, fireAt, checkpoint,
    stageCheckpoint, checkpointLabel,
  } = job.data;
  const jid = job.id ?? matchId;

  const parserMathOnly = job.data.parserMathOnly === true &&
    Number(stageCheckpoint ?? checkpoint) === 6;
  const checkpointRoute = parserMathOnly
    ? { accepted: true, stageCheckpoint: 6, calculatorCheckpoint: 6 }
    : resolveWorkerCheckpoint({ stageCheckpoint, checkpoint });
  const publicStageCheckpoint = checkpointRoute.stageCheckpoint;
  const triggerCheckpoint = checkpointRoute.calculatorCheckpoint;

  if (!checkpointRoute.accepted) {
    log(jid, 'info', `↷ Stage checkpoint #${publicStageCheckpoint || 'unknown'} ` +
        `(${checkpointLabel || 'unlabelled'}) skipped: worker is in legacy Q2/HT/Q4 mode. ` +
        `Set WORKER_ENABLE_FIVE_CHECKPOINTS=true after calculator support is ready.`);
    await job.updateProgress(100);
    return {
      matchId,
      skipped: true,
      reason: 'WORKER_LEGACY_Q2_HT_Q4_ONLY',
      stageCheckpoint: publicStageCheckpoint || null,
      checkpoint: null,
      decision: null,
      completedAt: new Date().toISOString(),
    };
  }

  log(jid, 'info', `Starting ${parserMathOnly ? 'final parser + math snapshot' : 'analysis'} — ${home} vs ${away} [${league}] ` +
      `(scheduled fire: ${fireAt}; stage checkpoint: ${publicStageCheckpoint || 'unknown'}` +
      `${checkpointLabel ? `/${checkpointLabel}` : ''}; calculator checkpoint: ${triggerCheckpoint})`);

  // ── Step 1: Node parser ───────────────────────────────────────────────────
  const parserScript = path.join(APP_ROOT, 'src', 'match_h2h_export.js');
  log(jid, 'info', `Step 1 → node ${parserScript} --matchId ${matchId} --home ${homeSlug} --away ${awaySlug}` +
      (parserMathOnly ? ' --skip-lines' : ''));
  await job.updateProgress(10);

  const parserArgs = [
    parserScript, '--matchId', matchId, '--home', homeSlug, '--away', awaySlug,
  ];
  if (parserMathOnly) parserArgs.push('--skip-lines');
  const parserResult = await run(NODE_BIN, parserArgs, { jobId: jid });

  log(jid, 'info', `Step 1 exited ${parserResult.code}${parserResult.stderr ? '\n' + parserResult.stderr : ''}`);
  if (parserResult.code !== 0) {
    throw new Error(`Parser failed (exit ${parserResult.code}): ${parserResult.stderr || '(none)'}`);
  }

  // Парсер может выйти с кодом 0, даже если не нашёл матч на betking
  // и не записал итоговый JSON (например, "Match not found" залогирован
  // как non-fatal warning внутри match_h2h_export.js). Проверяем файлы
  // на диске явно, чтобы не улетать на Step 2 с несуществующими путями.
  const dataFilePath = path.join(APP_ROOT, 'src', 'data', dataFilename);
  const lineFilePath = path.join(APP_ROOT, 'src', 'data', lineFilename);

  const missing = [dataFilePath, lineFilePath].filter(p => !fs.existsSync(p));
  if (missing.length) {
    throw new Error(
      `Parser exited 0 but did not produce expected file(s): ${missing.join(', ')} ` +
      `— likely failed to locate the match on the source site (see Step 1 logs above).`
    );
  }

  // Only calculated artifacts are checkpoint-specific. math_script.py merges
  // the current parser JSON and lines itself, so duplicating both source files
  // for every checkpoint is unnecessary.
  fs.mkdirSync(MATCH_FILES_DIR, { recursive: true });
  const checkpointKey = ENABLE_FIVE_CHECKPOINTS
    ? `checkpoint_${triggerCheckpoint}`
    : (triggerCheckpoint >= 1 && triggerCheckpoint <= 3
      ? `q${triggerCheckpoint}`
      : `checkpoint_${Date.now()}`);
  const matchBaseName = path.basename(dataFilePath, path.extname(dataFilePath));
  await job.updateProgress(40);

  // ── Step 2: Python calculator ─────────────────────────────────────────────
  const calcScript = path.join(APP_ROOT, 'src', 'math_script.py');
  const calculatedFilePath = path.join(
    MATCH_FILES_DIR,
    parserMathOnly
      ? `${matchBaseName}_result_checkpoint6.json`
      : `${matchBaseName}_${checkpointKey}_result.json`
  );

  log(jid, 'info', `Step 2 → ${PYTHON_BIN} ${calcScript} ${dataFilePath} ${lineFilePath} --output ${calculatedFilePath}`);

  const calcResult = await run(PYTHON_BIN, [calcScript, dataFilePath, lineFilePath, '--output', calculatedFilePath], { jobId: jid });
  log(jid, 'info', `Step 2 exited ${calcResult.code}${calcResult.stderr ? '\n' + calcResult.stderr : ''}`);
  if (calcResult.code !== 0) {
    throw new Error(`Calculator failed (exit ${calcResult.code}): ${calcResult.stderr || '(none)'}`);
  }

  await job.updateProgress(70);

  if (parserMathOnly) {
    await job.updateProgress(100);
    log(jid, 'info', `✓ Checkpoint #6 parser + math snapshot saved: ${calculatedFilePath}`);
    return {
      matchId,
      parserExitCode: parserResult.code,
      calcExitCode: calcResult.code,
      superBasketExitCode: null,
      decision: null,
      aiVerdict: null,
      checkpoint: 6,
      stageCheckpoint: 6,
      parserMathOnly: true,
      calculatedFilePath,
      superBasketOutputPath: null,
      outputStatus: 'PARSER_MATH_ONLY',
      completedAt: new Date().toISOString(),
    };
  }

  // ── Step 3: SUPER BASKET v15.3 ────────────────────────────────────────────
  // calculatedFilePath is the checkpoint snapshot. If it already contains
  // super_basket_calculation.market_evaluations, v15.3 reuses that audited
  // legacy calculation. Regardless, v15.3 loads the score model itself and
  // derives the stage from the JSON, so there is no --checkpoint argument.
  const requiredV15Files = [
    SUPER_BASKET_V15_SCRIPT,
    SCORE_MODEL_SCRIPT,
    LEGACY_ADVISOR_SCRIPT,
    CALIBRATION_FILE,
  ];
  const missingV15Files = requiredV15Files.filter(p => !fs.existsSync(p));
  if (missingV15Files.length) {
    throw new Error(`Missing SUPER BASKET v15.3 file(s): ${missingV15Files.join(', ')}`);
  }

  const superBasketOutputPath = path.join(
    MATCH_FILES_DIR,
    `${matchBaseName}_${checkpointKey}_v15_3_result.json`
  );

  const superBasketArgs = [
    SUPER_BASKET_V15_SCRIPT, 'run',
    '--match', calculatedFilePath,
    '--output', superBasketOutputPath,
    '--db', SUPER_BASKET_DB,
    '--score-model', SCORE_MODEL_SCRIPT,
    '--advisor', LEGACY_ADVISOR_SCRIPT,
    '--calibration', CALIBRATION_FILE,
    '--simulations', String(V15_SIMULATIONS),
    '--telegram',
  ];

  // Optional: v15.3 also reads SUPER_BASKET_BANKROLL / _USDT from the inherited
  // environment. Passing --bankroll-usdt is therefore unnecessary here.
  log(jid, 'info', `Step 3 → ${PYTHON_BIN} ${SUPER_BASKET_V15_SCRIPT} run ` +
      `--match ${calculatedFilePath} --output ${superBasketOutputPath} ` +
      `--score-model ${SCORE_MODEL_SCRIPT} --advisor ${LEGACY_ADVISOR_SCRIPT} ` +
      `--calibration ${CALIBRATION_FILE} --simulations ${V15_SIMULATIONS} --telegram`);

  const superBasketResult = await run(PYTHON_BIN, superBasketArgs, { jobId: jid });
  log(jid, 'info', `Step 3 exited ${superBasketResult.code}${superBasketResult.stderr ? '\n' + superBasketResult.stderr : ''}`);
  await job.updateProgress(90);

  let summary = null;
  let decision = null;
  let telegramStatus = null;
  let outputStatus = 'ERROR';

  if (superBasketResult.code === 0) {
    try {
      // stdout is only a compact CLI summary. The complete selected record,
      // including status/stake/blockers, is stored in the output JSON.
      summary = JSON.parse(superBasketResult.stdout);
      const fullResult = JSON.parse(fs.readFileSync(superBasketOutputPath, 'utf8'));
      decision = fullResult.selected ?? null;
      telegramStatus = fullResult.telegram?.delivery?.status ?? summary.telegram?.status ?? null;
      outputStatus = 'OK';
      log(jid, 'info',
        `Step 3 completed — decision: ${decision?.action ?? summary.action ?? 'n/a'}` +
        `/${decision?.status ?? 'n/a'} | stage: ${summary.stage ?? 'n/a'}` +
        ` | telegram: ${telegramStatus ?? 'n/a'}`
      );
    } catch (e) {
      outputStatus = 'OUTPUT_PARSE_ERROR';
      log(jid, 'error', `Step 3 result parse failed (non-fatal): ${e.message}`);
    }
  } else {
    log(jid, 'error', `Step 3 failed (non-fatal, exit ${superBasketResult.code})`);
  }

  await job.updateProgress(100);
  log(jid, 'info', `✓ Full chain completed for match ${matchId}`);

  return {
    matchId,
    parserExitCode: parserResult.code,
    calcExitCode:   calcResult.code,
    superBasketExitCode: superBasketResult.code,
    decision,
    aiVerdict:      decision?.action ?? summary?.action ?? null,
    checkpoint:     triggerCheckpoint || null,
    stageCheckpoint: publicStageCheckpoint || null,
    calculatedFilePath,
    superBasketOutputPath,
    outputStatus,
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
  ` | Concurrency: ${worker.opts.concurrency}` +
  ` | Checkpoint mode: ${ENABLE_FIVE_CHECKPOINTS ? 'FULL_1_TO_5' : 'LEGACY_Q2_HT_Q4_ONLY'}`
);
