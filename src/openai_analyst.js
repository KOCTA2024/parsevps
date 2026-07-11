'use strict';

/**
 * openai_analyst.js
 * Step 3 in the worker chain: reads match data files and runs OpenAI analysis.
 *
 * Public API:
 *   analyseMatch(jobData, dataFilePath, lineFilePath) → Promise<AnalysisResult>
 *
 * Промпт больше не жёстко привязан к одному .docx файлу — вместо этого скрипт
 * сканирует директорию PROMPTS_DIR и склеивает все поддерживаемые файлы
 * (.docx, .pdf, .json, .txt, .md) в один системный промпт, в алфавитном
 * порядке имён файлов (для стабильности между перезапусками).
 *
 * Env vars:
 *   OPENAI_API_KEY        — required
 *   OPENAI_MODEL          — default: gpt-5.4
 *   OPENAI_MAX_TOKENS     — default: 32000
 *   OPENAI_REASONING_EFFORT — default: low (none|low|medium|high|xhigh)
 *   OPENAI_TIMEOUT_MS     — default: 180000 (3 min)
 *   ANALYSIS_OUTPUT_DIR   — default: <APP_ROOT>/data
 *   PROMPTS_DIR           — default: <APP_ROOT>/src/prompts
 *                           Папка сканируется целиком; поддерживаются:
 *                           .docx, .pdf, .json, .txt, .md
 */

import fs    from 'fs';
import path  from 'path';
import https from 'https';
import { fileURLToPath }    from 'url';
import { createRequire }    from 'module';

// mammoth и pdf-parse — CommonJS пакеты, импортируем через createRequire
const require   = createRequire(import.meta.url);
const mammoth   = require('mammoth');
const pdfParse  = require('pdf-parse');

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const APP_ROOT  = path.resolve(__dirname, '..');

// ─── Config ──────────────────────────────────────────────────────────────────

const OPENAI_API_KEY    = process.env.OPENAI_API_KEY;
const OPENAI_MODEL      = process.env.OPENAI_MODEL      || 'gpt-5.4';
const OPENAI_MAX_TOKENS       = Number(process.env.OPENAI_MAX_TOKENS)       || 32_000;
const OPENAI_REASONING_EFFORT = process.env.OPENAI_REASONING_EFFORT || 'low'; // none | low | medium | high | xhigh
const OPENAI_TIMEOUT_MS = Number(process.env.OPENAI_TIMEOUT_MS) || 180_000;
const OUTPUT_DIR        = process.env.ANALYSIS_OUTPUT_DIR
  ? path.resolve(process.env.ANALYSIS_OUTPUT_DIR)
  : path.join(APP_ROOT, 'data');

const PROMPTS_DIR = process.env.PROMPTS_DIR
  ? path.resolve(process.env.PROMPTS_DIR)
  : path.join(APP_ROOT, 'src', 'prompts');

const SUPPORTED_EXT = ['.docx', '.pdf', '.json', '.txt', '.md'];

// ─── JSON output instruction appended to the prompt ──────────────────────────
// Кладём отдельно, чтобы можно было менять формат ответа, не трогая файлы промпта.

const JSON_OUTPUT_INSTRUCTION = `

---
ВАЖЛИВО: відповідь має бути ТІЛЬКИ у форматі JSON (без markdown-обгортки, без \`\`\`):
{
  "verdict": "PLAY|STRONG PLAY|PASS|CONFLICT",
  "recommendations": [
    {
      "market": "...",
      "line": "...",
      "side": "...",
      "p_final": 0.00,
      "reasoning": "..."
    }
  ],
  "data_quality": {
    "sample_a": null,
    "sample_b": null,
    "pooled": null,
    "h2h": null,
    "stat_support": "ON|OFF|unknown",
    "missing_fields": []
  },
  "live_projection": {
    "team_a": null,
    "team_b": null,
    "total": null,
    "margin": null
  },
  "p_final_table": [
    {
      "rank": 1,
      "market_side": "...",
      "p_hist": null,
      "p_scenario": null,
      "p_live_used": null,
      "weights": "...",
      "p_raw": null,
      "caps_blockers": "...",
      "p_final": null,
      "verdict": "PLAY|PASS"
    }
  ],
  "summary": "Короткий текст для читання людиною (1–5 речень)"
}`;

// ─── Извлечение текста из файла по расширению ────────────────────────────────

async function extractTextFromFile(filePath) {
  const ext = path.extname(filePath).toLowerCase();

  if (ext === '.docx') {
    const { value: text, messages } = await mammoth.extractRawText({ path: filePath });
    if (messages && messages.length > 0) {
      for (const m of messages) {
        if (m.type === 'error') {
          console.warn(`[openai_analyst] mammoth warning (${path.basename(filePath)}): ${m.message}`);
        }
      }
    }
    return text;
  }

  if (ext === '.pdf') {
    const buffer = fs.readFileSync(filePath);
    const { text } = await pdfParse(buffer);
    return text;
  }

  if (ext === '.json') {
    // Не конкатенируем JSON как plain text — парсим и красиво форматируем,
    // чтобы модель видела чистую структуру, а не сырую "простыню".
    const raw = fs.readFileSync(filePath, 'utf8');
    try {
      const parsed = JSON.parse(raw);
      return JSON.stringify(parsed, null, 2);
    } catch (e) {
      console.warn(`[openai_analyst] Invalid JSON in ${path.basename(filePath)}: ${e.message}. Using raw content.`);
      return raw;
    }
  }

  if (ext === '.txt' || ext === '.md') {
    return fs.readFileSync(filePath, 'utf8');
  }

  return null; // неподдерживаемое расширение — вызывающий код это отфильтрует заранее
}

// ─── Сканирование директории промптов ────────────────────────────────────────

function listPromptFiles(dir) {
  if (!fs.existsSync(dir)) {
    throw new Error(`[openai_analyst] Prompts directory not found: ${dir}`);
  }

  return fs.readdirSync(dir)
    .filter(name => SUPPORTED_EXT.includes(path.extname(name).toLowerCase()))
    .sort() // стабильный порядок между перезапусками — важно для консистентности промпта
    .map(name => path.join(dir, name));
}

// ─── Сборка системного промпта из всех файлов ────────────────────────────────

async function loadSystemPrompt() {
  const files = listPromptFiles(PROMPTS_DIR);

  if (files.length === 0) {
    throw new Error(`[openai_analyst] No supported prompt files found in ${PROMPTS_DIR} (expected: ${SUPPORTED_EXT.join(', ')})`);
  }

  const parts = [];

  for (const filePath of files) {
    const fileName = path.basename(filePath);
    try {
      const text = await extractTextFromFile(filePath);
      if (!text || text.trim().length === 0) {
        console.warn(`[openai_analyst] Empty content from ${fileName}, skipping.`);
        continue;
      }
      parts.push(`### Джерело: ${fileName}\n\n${text.trim()}`);
      console.log(`[openai_analyst] Loaded prompt part: ${fileName} (${text.length} chars)`);
    } catch (e) {
      console.error(`[openai_analyst] Failed to read ${fileName}: ${e.message}`);
    }
  }

  if (parts.length === 0) {
    throw new Error(`[openai_analyst] All prompt files in ${PROMPTS_DIR} failed to load or were empty.`);
  }

  const combined = parts.join('\n\n---\n\n');

  if (combined.length < 100) {
    throw new Error(`[openai_analyst] Combined prompt is suspiciously short (${combined.length} chars). Check files in ${PROMPTS_DIR}.`);
  }

  return combined + JSON_OUTPUT_INSTRUCTION;
}

// Кэш на уровне модуля — грузится один раз при первом вызове analyseMatch()
let _systemPromptCache = null;

async function getSystemPrompt() {
  if (!_systemPromptCache) {
    _systemPromptCache = await loadSystemPrompt();
    console.log(
      `[openai_analyst] System prompt assembled from ${PROMPTS_DIR} ` +
      `(${_systemPromptCache.length} chars total)`
    );
  }
  return _systemPromptCache;
}

/**
 * Сбросить кэш промпта (полезно, если обновили файлы в PROMPTS_DIR без рестарта).
 */
export function reloadPrompt() {
  _systemPromptCache = null;
  console.log('[openai_analyst] Prompt cache cleared — will reload on next call.');
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function readFileSafe(filePath) {
  try {
    if (fs.existsSync(filePath)) return fs.readFileSync(filePath, 'utf8');
  } catch (_) {}
  return null;
}

function openaiRequest(payload, timeoutMs) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify(payload);

    const req = https.request(
      {
        hostname: 'api.openai.com',
        path:     '/v1/chat/completions',
        method:   'POST',
        headers: {
          'Content-Type':   'application/json',
          'Authorization':  `Bearer ${OPENAI_API_KEY}`,
          'Content-Length': Buffer.byteLength(body),
        },
      },
      (res) => {
        const chunks = [];
        res.on('data', c => chunks.push(c));
        res.on('end', () => {
          try {
            const text = Buffer.concat(chunks).toString('utf8');
            const json = JSON.parse(text);
            if (res.statusCode >= 400) {
              reject(new Error(`OpenAI API error ${res.statusCode}: ${json.error?.message ?? text}`));
            } else {
              resolve(json);
            }
          } catch (e) {
            reject(new Error(`OpenAI response parse error: ${e.message}`));
          }
        });
      }
    );

    const timer = setTimeout(
      () => req.destroy(new Error(`OpenAI request timed out after ${timeoutMs}ms`)),
      timeoutMs
    );

    req.on('error', e => { clearTimeout(timer); reject(e); });
    req.on('close', ()  => clearTimeout(timer));
    req.write(body);
    req.end();
  });
}

function parseModelReply(text) {
  const stripped = text
    .replace(/^```(?:json)?\s*/i, '')
    .replace(/\s*```$/, '')
    .trim();
  return JSON.parse(stripped);
}

// ─── Notification hook ────────────────────────────────────────────────────────

let _notifier = async (_result) => {};

export function setNotifier(fn) {
  _notifier = fn;
}

// ─── Main export ──────────────────────────────────────────────────────────────

/**
 * analyseMatch
 *
 * @param {object}   jobData      — job payload from worker
 * @param {string}   dataFilePath — path to data/<home>_vs_<away>_<id>.json
 * @param {string}   lineFilePath — path to data/line_result_<id>.json
 * @param {object}   [options]
 * @param {Function} [options.notifier] — per-call notifier override
 * @returns {Promise<object>}
 */
export async function analyseMatch(jobData, dataFilePath, lineFilePath, options = {}) {
  const { matchId, home, away, league, kickoff } = jobData;
  const notifier = options.notifier ?? _notifier;

  if (!OPENAI_API_KEY) throw new Error('[openai_analyst] OPENAI_API_KEY is not set.');

  // ── Load prompt (docx + pdf + json + txt/md из PROMPTS_DIR) ───────────────
  const systemPrompt = await getSystemPrompt();

  // ── Assemble match context ────────────────────────────────────────────────
  const dataRaw = readFileSafe(dataFilePath);
  const lineRaw = readFileSafe(lineFilePath);

  if (!dataRaw && !lineRaw) {
    throw new Error(`[openai_analyst] Both data files missing for match ${matchId}.`);
  }

  const userMessage = [
    `Матч: ${home} vs ${away}`,
    `Ліга: ${league || 'невідома'}`,
    `Кік-оф: ${kickoff || 'невідомий'}`,
    '',
    dataRaw
      ? `=== Дані матчу (h2h / статистика) ===\n${dataRaw}`
      : '=== Дані матчу: файл відсутній ===',
    '',
    lineRaw
      ? `=== Результати математичного розрахунку (math_script) ===\n${lineRaw}`
      : '=== Результати math_script: файл відсутній ===',
  ].join('\n');

  // ── Call OpenAI ───────────────────────────────────────────────────────────
  const payload = {
    model:       OPENAI_MODEL,
    max_tokens:       OPENAI_MAX_TOKENS,
    reasoning_effort: OPENAI_REASONING_EFFORT,
    messages: [
      { role: 'system', content: systemPrompt },
      { role: 'user',   content: userMessage  },
    ],
  };

  let rawReply;
  try {
    const response = await openaiRequest(payload, OPENAI_TIMEOUT_MS);
    rawReply = response.choices?.[0]?.message?.content ?? '';
  } catch (err) {
    throw new Error(`[openai_analyst] OpenAI call failed for match ${matchId}: ${err.message}`);
  }

  // ── Parse reply ───────────────────────────────────────────────────────────
  let parsed;
  try {
    parsed = parseModelReply(rawReply);
  } catch (_) {
    parsed = {
      verdict:         'PARSE_ERROR',
      recommendations: [],
      summary:         rawReply.slice(0, 500),
      _raw:            rawReply,
    };
  }

  // ── Build & persist result ────────────────────────────────────────────────
  const result = {
    matchId,
    home,
    away,
    league:     league || '',
    kickoff:    kickoff || null,
    analysedAt: new Date().toISOString(),
    model:      OPENAI_MODEL,
    ...parsed,
  };

  const outPath = path.join(OUTPUT_DIR, `analysis_${matchId}.json`);
  try {
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
    fs.writeFileSync(outPath, JSON.stringify(result, null, 2), 'utf8');
  } catch (e) {
    console.error(`[openai_analyst] Could not write ${outPath}:`, e.message);
  }

  // ── Notify ────────────────────────────────────────────────────────────────
  try {
    await notifier(result);
  } catch (e) {
    console.error('[openai_analyst] Notifier threw:', e.message);
  }

  return result;
}