'use strict';

/**
 * test_e2e_three_quarters.js
 *
 * Автономный e2e-тест боевой цепочки без Redis/BullMQ:
 *   Step 1 (node match_h2h_export.js) → Step 2 (python math_script.py)
 *   → Step 2.5 (data sufficiency, как в worker.js) → Step 3 (FAKE, без OpenAI)
 *   → Notify (FAKE Telegram, СТРОГО в один chat_id) → cleanup
 *
 * Гоняется 3 раза подряд на одном и том же матче — симулирует то, что
 * stage-monitor триггерит анализ после конца 1-й, 2-й и 3-й четверти.
 * Никакой очереди/воркера не поднимается — процесс просто завершается
 * после 3 прогонов (process.exit), "останавливать" отдельно нечего.
 *
 * НИЧЕГО не пишет в state/telegram_chats.json и не шлёт реальным подписчикам
 * бота — только в chat_id, который ты передашь явно.
 *
 * ── Запуск ────────────────────────────────────────────────────────────────
 *
 *   TEST_CHAT_ID=123456789 \
 *   TELEGRAM_TOKEN=xxx:yyy \
 *   node test_e2e_three_quarters.js \
 *     --match-url "https://www.flashscore.com/match/xxx/?mid=12345" \
 *     --league "Some League"
 *
 * Либо без --match-url — тогда скрипт попробует взять первый матч
 * из matches.json (см. --matches-file / MATCHES_FILE); объект матча там
 * должен содержать matchUrl (или url / match_url).
 *
 * Флаги:
 *   --match-url URL       ссылка на матч (flashscore) — matchId вытаскивается
 *                          из ?mid=; home/away/имена команд подтянутся сами
 *                          после Step 1, как в интерактивном режиме
 *                          match_h2h_export.js
 *   --league NAME         название лиги для уведомлений (опционально)
 *   --runs N              сколько раз прогнать цепочку (default 3)
 *   --force-ai            форсировать прохождение Step 2.5, даже если
 *                          реальных данных объективно недостаточно
 *                          (default: true — иначе тест может не дойти
 *                          до уведомления на реальном матче)
 *   --no-force-ai         не форсировать — вести себя как прод
 *   --matches-file PATH   путь к matches.json (default: MATCHES_FILE env
 *                          или /app/state/matches.json)
 *   --dry-run-telegram    не слать реальный HTTP-запрос в Telegram,
 *                          только напечатать, что было бы отправлено
 */

import path from 'path';
import fs from 'fs';
import https from 'https';
import { execFile } from 'child_process';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// APP_ROOT — предполагается тот же layout, что и в worker.js: этот файл
// лежит в src/, APP_ROOT на уровень выше. Если кладёшь тест в другое место —
// поправь APP_ROOT_OVERRIDE.
const APP_ROOT_OVERRIDE = process.env.APP_ROOT;
const APP_ROOT = APP_ROOT_OVERRIDE
  ? path.resolve(APP_ROOT_OVERRIDE)
  : path.resolve(__dirname); // скрипт лежит прямо в src/ рядом с worker.js, match_h2h_export.js и data/ — тоже здесь

const NODE_BIN   = process.execPath;
const PYTHON_BIN = process.env.PYTHON_BIN || 'python3';

// ─── CLI args ────────────────────────────────────────────────────────────────

function parseArgs(argv) {
  const out = { runs: 3, forceAi: true, dryRunTelegram: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    switch (a) {
      case '--match-url':   out.matchUrl  = argv[++i]; break;
      case '--league':      out.league    = argv[++i]; break;
      case '--runs':        out.runs      = Number(argv[++i]) || 3; break;
      case '--matches-file':out.matchesFile = argv[++i]; break;
      case '--force-ai':    out.forceAi   = true; break;
      case '--no-force-ai': out.forceAi   = false; break;
      case '--dry-run-telegram': out.dryRunTelegram = true; break;
      default:
        console.warn(`[test] Unknown arg ignored: ${a}`);
    }
  }
  return out;
}

const args = parseArgs(process.argv.slice(2));

// Та же логика, что extractMid() в match_h2h_export.js — вытаскиваем ?mid=
// из ссылки на матч. Фоллбэк на последний path-сегмент на случай ссылок
// вида .../match/<id>/ без query-параметра.
function extractMatchId(matchUrl) {
  try {
    const mid = new URL(matchUrl).searchParams.get('mid');
    if (mid) return mid;
  } catch (_) { /* невалидный/относительный URL — пробуем фоллбэк ниже */ }
  const m = String(matchUrl || '').match(/([A-Za-z0-9]{6,})\/?(?:#.*)?$/);
  if (m) return m[1];
  throw new Error(`Не смог вытащить matchId из --match-url "${matchUrl}"`);
}

const TEST_CHAT_ID = process.env.TEST_CHAT_ID || args.chatId;
if (!TEST_CHAT_ID && !args.dryRunTelegram) {
  console.error(
    '[test] Не задан TEST_CHAT_ID (env) — некуда слать уведомление.\n' +
    '       Либо задай TEST_CHAT_ID=<chat_id>, либо запусти с --dry-run-telegram.'
  );
  process.exit(1);
}

// ─── Выбор матча: явные флаги ИЛИ первый матч из matches.json ───────────────

function pickFirstMatchFromFile(filePath) {
  let raw;
  try {
    raw = fs.readFileSync(filePath, 'utf8');
  } catch (e) {
    console.warn(`[test] Не смог прочитать matches-file ${filePath}: ${e.message}`);
    return null;
  }

  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (e) {
    console.warn(`[test] matches-file ${filePath} не валидный JSON: ${e.message}`);
    return null;
  }

  // matches.json может быть массивом, либо объектом { matches: [...] },
  // либо объектом-словарём { "<id>": {...}, ... } — пробуем все варианты.
  let list = null;
  if (Array.isArray(parsed)) list = parsed;
  else if (Array.isArray(parsed.matches)) list = parsed.matches;
  else if (typeof parsed === 'object') list = Object.values(parsed);

  if (!Array.isArray(list) || list.length === 0) {
    console.warn(`[test] matches-file ${filePath} не содержит распознаваемого списка матчей.`);
    return null;
  }

  const m = list[0];

  // Пытаемся угадать реальные имена полей — НЕ видел исходник stage_monitor.js /
  // monitor.js, так что это best-effort. Если не подошло — см. предупреждение
  // ниже и передай матч явно через --match-url.
  const matchUrl = m.matchUrl ?? m.url ?? m.match_url;
  const league   = m.league   ?? m.competition ?? m.leagueName ?? '';

  if (!matchUrl) {
    console.warn(
      `[test] Не смог однозначно вытащить matchUrl из ${filePath}.\n` +
      `       Сырой первый элемент: ${JSON.stringify(m)}\n` +
      `       Передай матч явно через --match-url.`
    );
    return null;
  }

  let matchId;
  try {
    matchId = m.matchId ?? m.id ?? m.match_id ?? extractMatchId(matchUrl);
  } catch (e) {
    console.warn(`[test] ${e.message}`);
    return null;
  }

  // home/away — best-effort, если есть в файле; иначе подтянутся из
  // результата Step 1 (парсер сам их скрапит по matchUrl).
  const home = m.home ?? m.homeTeam ?? m.home_team ?? null;
  const away = m.away ?? m.awayTeam ?? m.away_team ?? null;

  return { matchUrl, matchId: String(matchId), home, away, league };
}

function resolveMatch() {
  if (args.matchUrl) {
    let matchId;
    try {
      matchId = extractMatchId(args.matchUrl);
    } catch (e) {
      console.error(`[test] ${e.message}`);
      process.exit(1);
    }
    return {
      matchUrl: args.matchUrl,
      matchId,
      home:     null, // подтянется из данных Step 1 (парсер скрапит сам)
      away:     null,
      league:   args.league || '',
    };
  }

  const matchesFile = args.matchesFile
    || process.env.MATCHES_FILE
    || '/app/state/matches.json';

  const fromFile = pickFirstMatchFromFile(matchesFile);
  if (fromFile) return fromFile;

  console.error(
    '[test] Не удалось определить матч ни из флагов, ни из matches.json.\n' +
    '       Передай явно: --match-url "<ссылка на матч>"'
  );
  process.exit(1);
}

const MATCH = resolveMatch();

// ─── Step 2.5: data sufficiency (копия логики из worker.js) ────────────────
// Держим её тут же, чтобы тест вёл себя предсказуемо и было видно в логах,
// прошёл бы реальный прод-гейт или нет — даже если --force-ai его обходит.

const MIN_TEAM_VALID_GAMES   = Number(process.env.MIN_TEAM_VALID_GAMES)   || 5;
const MIN_POOLED_VALID_GAMES = Number(process.env.MIN_POOLED_VALID_GAMES) || 10;
const REQUIRED_LINE_MARKETS  = ['match_total', 'match_handicap'];
const REQUIRED_STAT_FIELDS = [
  'hflsm', 'aflsm', 'hrbm', 'arbm', 'hastm', 'aastm',
  'hstlm', 'astlm', 'hblkm', 'ablkm', 'htovm', 'atovm',
];
const MIN_STAT_COVERAGE_RATIO = Number(process.env.MIN_STAT_COVERAGE_RATIO) || 0.7;

function hasDetailedStats(game) {
  return REQUIRED_STAT_FIELDS.every((field) => {
    const v = game?.[field];
    return v !== undefined && v !== null && String(v).trim() !== '';
  });
}

function checkStatsCoverage(games, label) {
  if (!Array.isArray(games) || games.length === 0) {
    return `${label}: no history games found for detailed-stats check`;
  }
  const withStats = games.filter(hasDetailedStats).length;
  const ratio = withStats / games.length;
  if (ratio < MIN_STAT_COVERAGE_RATIO) {
    return `${label}: only ${withStats}/${games.length} (${(ratio * 100).toFixed(0)}%) have full stats, need ≥${(MIN_STAT_COVERAGE_RATIO * 100).toFixed(0)}%`;
  }
  return null;
}

function checkDataSufficiency(dataFilePath, lineFilePath) {
  let lineData;
  try {
    lineData = JSON.parse(fs.readFileSync(lineFilePath, 'utf8'));
  } catch (e) {
    return `line file missing or invalid JSON (${e.message})`;
  }
  for (const market of REQUIRED_LINE_MARKETS) {
    const arr = lineData[market];
    if (!Array.isArray(arr) || arr.length === 0) {
      return `no lines for required market "${market}"`;
    }
  }

  let data;
  try {
    data = JSON.parse(fs.readFileSync(dataFilePath, 'utf8'));
  } catch (e) {
    return `data file missing or invalid JSON (${e.message})`;
  }

  const dq = data?.logic?.data_quality;
  if (!dq) return 'data_quality block missing from data file';
  if (dq.lines_stale_warning) return `lines are stale: ${dq.lines_stale_warning}`;
  if ((dq.team_a_valid_games ?? 0) < MIN_TEAM_VALID_GAMES)
    return `team A sample too small (${dq.team_a_valid_games ?? 0} < ${MIN_TEAM_VALID_GAMES})`;
  if ((dq.team_b_valid_games ?? 0) < MIN_TEAM_VALID_GAMES)
    return `team B sample too small (${dq.team_b_valid_games ?? 0} < ${MIN_TEAM_VALID_GAMES})`;
  if ((dq.pooled_valid_games ?? 0) < MIN_POOLED_VALID_GAMES)
    return `pooled sample too small (${dq.pooled_valid_games ?? 0} < ${MIN_POOLED_VALID_GAMES})`;

  const rawData = data?.raw_data;
  if (!rawData) return 'raw_data block missing from data file';

  const aIssue = checkStatsCoverage(rawData.team_a_hist, 'team A history');
  if (aIssue) return aIssue;
  const bIssue = checkStatsCoverage(rawData.team_b_hist, 'team B history');
  if (bIssue) return bIssue;

  return null;
}

// ─── Step 1 / Step 2 runner (как в worker.js) ───────────────────────────────

function run(bin, args_, opts = {}) {
  return new Promise((resolve, reject) => {
    const child = execFile(bin, args_, { cwd: APP_ROOT, ...opts }, (err, stdout, stderr) => {
      if (err && err.code === undefined) { reject(err); return; }
      resolve({ stdout: stdout.trim(), stderr: stderr.trim(), code: err ? err.code : 0 });
    });
    child.stdout?.pipe(process.stdout, { end: false });
    child.stderr?.pipe(process.stderr, { end: false });
  });
}

// ─── Чтение home/away/url из результата Step 1 ──────────────────────────────
// При --match-url имена команд заранее неизвестны — match_h2h_export.js сам
// их скрапит и кладёт в строку MAIN MATCH (поля ht/at/url).

function readMainMatchInfo(dataFilePath) {
  try {
    const payload = JSON.parse(fs.readFileSync(dataFilePath, 'utf8'));
    const row = Array.isArray(payload) ? payload.find(r => r && r.src === 'MAIN MATCH') : null;
    if (!row) return null;
    return { home: row.ht || null, away: row.at || null, url: row.url || null };
  } catch (_) {
    return null;
  }
}

// ─── FAKE Step 3 — никакого OpenAI, просто правдоподобная структура ─────────
// Схема повторяет JSON_OUTPUT_INSTRUCTION из openai_analyst.js, чтобы
// downstream-код (форматирование в Telegram и т.п.) вёл себя так же, как
// с настоящим ответом модели.

const FAKE_VERDICTS = ['STRONG PLAY', 'PLAY', 'PASS'];

function fakeAnalyseMatch(jobData, dataFilePath, lineFilePath, quarterLabel) {
  const { matchId, home, away, league, kickoff, url, matchUrl: sourceMatchUrl } = jobData;

  // url — реальная ссылка из строки MAIN MATCH (readMainMatchInfo, вызван
  // в runChainOnce сразу после Step 1); sourceMatchUrl — то, что передали
  // через --match-url, на случай если парсер её не заполнил.
  const matchUrl = url || sourceMatchUrl || null;

  const verdict = FAKE_VERDICTS[Math.floor(Math.random() * FAKE_VERDICTS.length)];

  const result = {
    matchId,
    home,
    away,
    league: league || '',
    kickoff: kickoff || null,
    analysedAt: new Date().toISOString(),
    model: 'FAKE-TEST-MODEL (no real OpenAI call)',
    verdict,
    recommendations: verdict === 'PASS' ? [] : [
      {
        market: 'match_total',
        line: '154.5',
        side: 'Over',
        p_final: 0.74,
        reasoning: `[ТЕСТ, не реальный анализ] Симуляция триггера после ${quarterLabel}: обидві команди тримають темп вище пулу, live-проекція вища за лінію.`,
      },
    ],
    data_quality: {
      sample_a: 7, sample_b: 8, pooled: 15, h2h: 2,
      stat_support: 'ON', missing_fields: [],
    },
    live_projection: { team_a: 76.4, team_b: 79.1, total: 155.5, margin: -2.7 },
    p_final_table: [
      {
        rank: 1, market_side: 'Total Over 154.5',
        p_hist: 0.68, p_scenario: 0.71, p_live_used: 0.74,
        weights: '0.4/0.3/0.3', p_raw: 0.72, caps_blockers: '-',
        p_final: 0.74, verdict: verdict === 'PASS' ? 'PASS' : 'PLAY',
      },
    ],
    summary: `[ТЕСТ — не реальний AI] Фейковий вердикт для перевірки ланцюжка після ${quarterLabel}.`,
    matchUrl,
    _isFakeTestResult: true,
  };

  // Мимикрируем побочный эффект настоящей analyseMatch — пишем analysis_<id>.json
  try {
    const outDir = process.env.ANALYSIS_OUTPUT_DIR
      ? path.resolve(process.env.ANALYSIS_OUTPUT_DIR)
      : path.join(APP_ROOT, 'data');
    fs.mkdirSync(outDir, { recursive: true });
    fs.writeFileSync(
      path.join(outDir, `analysis_${matchId}.json`),
      JSON.stringify(result, null, 2),
      'utf8'
    );
  } catch (e) {
    console.warn(`[test] Could not write fake analysis file: ${e.message}`);
  }

  return result;
}

// ─── FAKE Telegram notifier — СТРОГО в один chat_id, реальный HTTP (если не dry-run) ──
// Не использует state/telegram_chats.json и не рассылает всем подписчикам бота.

const BOT_TOKEN = process.env.TELEGRAM_TOKEN || process.env.TELEGRAM_KEY;

function emoji(verdict) {
  if (!verdict) return '❓';
  const v = verdict.toUpperCase();
  if (v.includes('STRONG')) return '🔥';
  if (v.includes('PLAY')) return '✅';
  if (v.includes('PASS')) return '⏸';
  return '❓';
}

function formatMessage(result, quarterLabel) {
  const { home, away, league, verdict, recommendations, summary, matchUrl } = result;
  const lines = [
    `🧪 <b>[TEST — ${quarterLabel}]</b>`,
    `🏀 <b>${home} vs ${away}</b>`,
    league ? `🏆 ${league}` : '',
    ``,
    `${emoji(verdict)} Вердикт: <b>${verdict}</b>`,
  ];
  for (const rec of recommendations || []) {
    const pct = rec.p_final != null ? ` — P_final: <b>${Math.round(rec.p_final * 100)}%</b>` : '';
    lines.push(`\n📌 ${rec.market} ${rec.line} <i>${rec.side}</i>${pct}`);
    if (rec.reasoning) lines.push(`   <i>${rec.reasoning.slice(0, 200)}</i>`);
  }
  if (summary) lines.push(`\n💬 ${summary}`);
  if (matchUrl) lines.push(`\n🔗 <a href="${matchUrl}">Матч</a>`);
  return lines.filter(l => l !== '').join('\n');
}

function apiRequest(method, payload) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify(payload);
    const req = https.request(
      {
        hostname: 'api.telegram.org',
        path: `/bot${BOT_TOKEN}/${method}`,
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
      },
      (res) => {
        const chunks = [];
        res.on('data', c => chunks.push(c));
        res.on('end', () => {
          try {
            const json = JSON.parse(Buffer.concat(chunks).toString('utf8'));
            if (!json.ok) reject(new Error(`Telegram error (${method}): ${json.description}`));
            else resolve(json.result);
          } catch (e) {
            reject(new Error(`Telegram parse error (${method}): ${e.message}`));
          }
        });
      }
    );
    req.on('error', reject);
    req.write(body);
    req.end();
  });
}

async function sendToTestChatOnly(result, quarterLabel) {
  const text = formatMessage(result, quarterLabel);

  if (args.dryRunTelegram || !BOT_TOKEN) {
    console.log(
      `[test][telegram-dry-run] Would send to chat_id=${TEST_CHAT_ID}:\n` +
      '─'.repeat(60) + `\n${text.replace(/<[^>]+>/g, '')}\n` + '─'.repeat(60)
    );
    return;
  }

  await apiRequest('sendMessage', {
    chat_id: TEST_CHAT_ID,
    text,
    parse_mode: 'HTML',
    disable_web_page_preview: true,
  });
  console.log(`[test][telegram] Sent to chat_id=${TEST_CHAT_ID} only (verdict: ${result.verdict}).`);
}

// ─── Один прогон цепочки (эквивалент processJob из worker.js, Step3 замокан) ─

async function runChainOnce(runIndex, quarterLabel) {
  console.log(`\n${'='.repeat(70)}`);
  console.log(`[test] RUN ${runIndex} — simulating trigger after ${quarterLabel}`);
  console.log('='.repeat(70));

  // Имя файла фиксируем через --fileName, не завязываясь на homeSlug/awaySlug —
  // при --match-url их заранее нет, парсер сам заслагифицирует скрапленные имена.
  const dataBaseName = `match_${MATCH.matchId}`;
  const dataFilename = `${dataBaseName}.json`;
  const lineFilename = `line_result_${MATCH.matchId}.json`;
  const dataFilePath = path.join(APP_ROOT, 'data', dataFilename);
  const lineFilePath = path.join(APP_ROOT, 'data', lineFilename);
  const parserScript = path.join(APP_ROOT, 'match_h2h_export.js');
  const calcScript   = path.join(APP_ROOT, 'math_script.py');

  // Step 1 — интерактивный режим match_h2h_export.js: только --matchUrl,
  // homeSlug/awaySlug парсер определит сам.
  console.log(`[test] Step 1 → node ${parserScript} --matchUrl ${MATCH.matchUrl} --fileName ${dataBaseName}`);
  const parserResult = await run(NODE_BIN, [
    parserScript, '--matchUrl', MATCH.matchUrl, '--fileName', dataBaseName,
  ]);
  if (parserResult.code !== 0) {
    throw new Error(`[run ${runIndex}] Parser failed (exit ${parserResult.code}): ${parserResult.stderr || '(none)'}`);
  }

  // Подтягиваем реальные home/away/url из свежесозданного файла — при
  // --match-url они заранее неизвестны.
  const mainInfo = readMainMatchInfo(dataFilePath);
  if (mainInfo) {
    if (mainInfo.home) MATCH.home = mainInfo.home;
    if (mainInfo.away) MATCH.away = mainInfo.away;
    if (mainInfo.url)  MATCH.url  = mainInfo.url;
  }

  // Step 2
  console.log(`[test] Step 2 → ${PYTHON_BIN} ${calcScript} ${dataFilePath} ${lineFilePath}`);
  const calcResult = await run(PYTHON_BIN, [calcScript, dataFilePath, lineFilePath]);
  if (calcResult.code !== 0) {
    throw new Error(`[run ${runIndex}] Calculator failed (exit ${calcResult.code}): ${calcResult.stderr || '(none)'}`);
  }

  // Step 2.5
  const skipReason = checkDataSufficiency(dataFilePath, lineFilePath);
  if (skipReason) {
    console.log(`[test] Step 2.5 gate: WOULD SKIP AI in prod — reason: ${skipReason}`);
    if (!args.forceAi) {
      console.log(`[test] --no-force-ai set — stopping run ${runIndex} before AI, as prod would.`);
      cleanup(dataFilePath, lineFilePath);
      return { skipped: true, skipReason };
    }
    console.log('[test] --force-ai (default) — bypassing gate to reach fake AI + notification anyway.');
  } else {
    console.log('[test] Step 2.5 gate: OK, prod would proceed to AI normally.');
  }

  // Step 3 (FAKE)
  const fakeResult = fakeAnalyseMatch(MATCH, dataFilePath, lineFilePath, quarterLabel);
  console.log(`[test] Step 3 (FAKE) → verdict: ${fakeResult.verdict}`);

  // Notify — только в TEST_CHAT_ID
  await sendToTestChatOnly(fakeResult, quarterLabel);

  cleanup(dataFilePath, lineFilePath);

  return { skipped: false, verdict: fakeResult.verdict };
}

function cleanup(dataFilePath, lineFilePath) {
  for (const filePath of [dataFilePath, lineFilePath]) {
    try {
      fs.unlinkSync(filePath);
      console.log(`[test] Cleaned up ${filePath}`);
    } catch (e) {
      console.log(`[test] Could not delete ${filePath}: ${e.message}`);
    }
  }
}

// ─── Main ────────────────────────────────────────────────────────────────────

const QUARTER_LABELS = ['1-й четверти (Q1)', '2-й четверти (Q2)', '3-й четверти (Q3)'];

async function main() {
  console.log(`[test] Match: ${MATCH.home || '?'} vs ${MATCH.away || '?'} [${MATCH.league || '?'}] id=${MATCH.matchId} url=${MATCH.matchUrl || '(из matches-file)'}`);
  console.log(`[test] Runs: ${args.runs} | force-ai: ${args.forceAi} | dry-run-telegram: ${args.dryRunTelegram}`);
  console.log(`[test] Notifications go ONLY to chat_id=${TEST_CHAT_ID || '(dry-run, none)'}\n`);

  const results = [];
  for (let i = 0; i < args.runs; i++) {
    const label = QUARTER_LABELS[i] || `запуск #${i + 1}`;
    try {
      const r = await runChainOnce(i + 1, label);
      results.push({ run: i + 1, label, ...r });
    } catch (e) {
      console.error(`[test] RUN ${i + 1} (${label}) FAILED: ${e.message}`);
      results.push({ run: i + 1, label, error: e.message });
    }
  }

  console.log(`\n${'='.repeat(70)}`);
  console.log('[test] SUMMARY');
  console.log('='.repeat(70));
  for (const r of results) {
    if (r.error) console.log(`  ✗ ${r.label}: FAILED — ${r.error}`);
    else if (r.skipped) console.log(`  ⏭ ${r.label}: skipped before AI — ${r.skipReason}`);
    else console.log(`  ✓ ${r.label}: OK — verdict=${r.verdict}`);
  }

  const anyFailed = results.some(r => r.error);
  console.log(`\n[test] Done. ${anyFailed ? 'SOME RUNS FAILED.' : 'All runs completed.'} Exiting.`);
  process.exit(anyFailed ? 1 : 0);
}

main().catch((e) => {
  console.error('[test] Fatal:', e);
  process.exit(1);
});