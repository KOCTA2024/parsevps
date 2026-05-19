/**
 * fetch_lines.js
 * ─────────────────────────────────────────────────────────────────────────────
 * Парсить лінії букмекерів з ds.lsapp.eu (GraphQL endpoint: findOddsByEventId)
 *
 * Що парситься (тільки active:true записи):
 *  • Фора матчу             HOME_AWAY / ASIAN_HANDICAP
 *                           scopes: FULL_TIME, FULL_TIME_OVER_TIME, FIRST_HALF,
 *                                   FIRST_QUARTER … FOURTH_QUARTER
 *  • Загальний тотал матчу  OVER_UNDER, FULL_TIME / FULL_TIME_OVER_TIME
 *  • Тотал чверті           OVER_UNDER, FIRST_QUARTER … FOURTH_QUARTER
 *  • Тотал H1/H2            OVER_UNDER, FIRST_HALF / SECOND_HALF
 *  • 1X2                    HOME_DRAW_AWAY, будь-який scope
 *  • Індивідуальні тотали   HOME_OVER_UNDER / AWAY_OVER_UNDER, будь-який scope
 *
 * Результат → OUTPUT_PATH/line_result.json
 */

import https from 'https';
import zlib  from 'zlib';
import fs    from 'fs';
import path  from 'path';

// ─── GraphQL endpoint ─────────────────────────────────────────────────────────

const GQL_HOST   = 'global.ds.lsapp.eu';
const GQL_HASH   = 'oce';             // Новий _hash для odds эндпоинта
const PROJECT_ID = '35';
const GEO_CODE   = 'UA';
const GEO_SUB    = 'UA30';

// ─── Класифікація bettingType ─────────────────────────────────────────────────

const TYPE_CATEGORY = {
  'HOME_DRAW_AWAY'  : 'match_1x2',
  'HOME_AWAY'       : 'match_handicap',
  'ASIAN_HANDICAP'  : 'match_handicap',
  'HANDICAP'        : 'match_handicap',
  'OVER_UNDER'      : 'total',
  'HOME_OVER_UNDER' : 'home_ind_total',
  'AWAY_OVER_UNDER' : 'away_ind_total',
};

// ─── Людські назви scope ──────────────────────────────────────────────────────

const SCOPE_LABEL = {
  'FULL_TIME'            : 'Match',
  'FULL_TIME_OVER_TIME'  : 'Match(OT)',
  'FIRST_HALF'           : 'H1',
  'SECOND_HALF'          : 'H2',
  'FIRST_QUARTER'        : 'Q1',
  'SECOND_QUARTER'       : 'Q2',
  'THIRD_QUARTER'        : 'Q3',
  'FOURTH_QUARTER'       : 'Q4',
  'FIRST_PERIOD'         : 'P1',
  'SECOND_PERIOD'        : 'P2',
  'THIRD_PERIOD'         : 'P3',
};

// ─── HTTP helper ──────────────────────────────────────────────────────────────

function httpGet(url, extraHeaders = {}) {
  return new Promise((resolve, reject) => {
    const urlObj  = new URL(url);
    const options = {
      hostname : urlObj.hostname,
      path     : urlObj.pathname + urlObj.search,
      method   : 'GET',
      headers  : {
        'User-Agent'     : 'Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0',
        'Accept'         : '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'Referer'        : 'https://www.flashscore.ua/',
        'Origin'         : 'https://www.flashscore.ua',
        'DNT'            : '1',
        'Sec-Fetch-Dest' : 'empty',
        'Sec-Fetch-Mode' : 'cors',
        'Sec-Fetch-Site' : 'cross-site',
        'Connection'     : 'keep-alive',
        'Sec-GPC'        : '1',
        ...extraHeaders,
      },
    };

    https.get(options, res => {
      const enc = res.headers['content-encoding'];
      let stream = res;
      if      (enc === 'gzip')    stream = res.pipe(zlib.createGunzip());
      else if (enc === 'deflate') stream = res.pipe(zlib.createInflate());
      else if (enc === 'br')      stream = res.pipe(zlib.createBrotliDecompress());

      const chunks = [];
      stream.on('data',  c => chunks.push(c));
      stream.on('end',   () => resolve(Buffer.concat(chunks).toString('utf-8')));
      stream.on('error', reject);
    }).on('error', reject);
  });
}


async function httpGetWithRetry(url, extraHeaders = {}, retries = 3, delayMs = 1500) {
  for (let i = 0; i < retries; i++) {
    try {
      const raw = await httpGet(url, extraHeaders);
      if (raw && raw.length >= 20) return raw;
      console.warn(`⚠ Спроба ${i + 1}: порожня відповідь, retry...`);
    } catch (e) {
      console.warn(`⚠ Спроба ${i + 1} помилка: ${e.message}`);
    }
    if (i < retries - 1) await new Promise(r => setTimeout(r, delayMs));
  }
  return '';
}
// ─── URL builder ──────────────────────────────────────────────────────────────

function buildLinesUrl(eventId) {
  const params = new URLSearchParams({
    _hash                : GQL_HASH,
    eventId              : eventId,
    projectId            : PROJECT_ID,
    geoIpCode            : GEO_CODE,
    geoIpSubdivisionCode : GEO_SUB,
  });
  // Додався шлях /odds/ перед pq_graphql
  return `https://${GQL_HOST}/odds/pq_graphql?${params.toString()}`;
}

// ─── Парсер відповіді findOddsByEventId ───────────────────────────────────────

/**
 * Структура відповіді:
 *   data.findOddsByEventId.odds[]   ← масив EventOdds
 *     .bookmakerId
 *     .bettingType
 *     .bettingScope
 *     .odds[]                       ← масив EventOddsItem
 *       .eventParticipantId         ← null = загальна ставка; non-null = конкретна команда
 *       .value                      ← поточний коефіцієнт
 *       .opening                    ← початковий коефіцієнт
 *       .active                     ← чи активна лінія
 *       .handicap.value             ← значення фори/тоталу
 *       .selection                  ← "OVER"/"UNDER" або null
 */
function parseOddsResponse(raw, bookmakersMap, participantsMap) {
  let data;
  try { data = JSON.parse(raw); }
  catch (e) {
    console.warn('⚠ line_result: не вдалося розпарсити JSON:', e.message);
    return null;
  }

  const root = data?.data?.findOddsByEventId ?? null;
  if (!root) {
    console.warn('⚠ line_result: невідома структура відповіді');
    return { _raw: data };
  }

  // Будуємо карту учасників id→name якщо передано
  // participantsMap = { "rJqHNXt2": "TeamHome", "zDW2oyZ8": "TeamAway" }

  const result = {
    meta: {
      bookmakers : Object.values(bookmakersMap),
      fetchedAt  : new Date().toISOString(),
    },
    match_1x2      : [],   // П1/Х/П2
    match_handicap : [],   // фора матчу (HOME_AWAY, ASIAN_HANDICAP)
    match_total    : [],   // тотал матчу (FULL_TIME / OT)
    half_total     : [],   // тотал H1 / H2
    quarter_total  : [],   // тотал Q1–Q4
    home_ind_total : [],   // індив. тотал дому
    away_ind_total : [],   // індив. тотал гостей
    other          : [],   // всі інші типи
  };

  for (const eventOdds of (root.odds ?? [])) {
    const {
      bookmakerId,
      bettingType,
      bettingScope,
      odds: oddsItems = [],
    } = eventOdds;

    const bmName    = bookmakersMap[bookmakerId] ?? String(bookmakerId);
    const scopeLabel= SCOPE_LABEL[bettingScope]  ?? bettingScope;
    const category  = TYPE_CATEGORY[bettingType]  ?? 'other';

    // ── Фільтруємо тільки активні лінії ──────────────────────────────────────
    const activeItems = oddsItems.filter(o => o.active === true);
    if (activeItems.length === 0) continue;

    // ── Нормалізація залежно від типу ─────────────────────────────────────────

    if (category === 'match_1x2') {
      // HOME_DRAW_AWAY: 3 рядки — home/draw/away за eventParticipantId
      // eventParticipantId: non-null = команда, null = нічия
      const homeItem = activeItems.find(o => o.eventParticipantId === participantsMap?.homeId);
      const awayItem = activeItems.find(o => o.eventParticipantId === participantsMap?.awayId);
      const drawItem = activeItems.find(o => o.eventParticipantId === null);

      result.match_1x2.push({
        bookmaker : bmName,
        scope     : scopeLabel,
        bettingType,
        homeOdd   : homeItem?.value   ?? null,
        drawOdd   : drawItem?.value   ?? null,
        awayOdd   : awayItem?.value   ?? null,
        homeOddOpen: homeItem?.opening ?? null,
        awayOddOpen: awayItem?.opening ?? null,
      });

    } else if (category === 'match_handicap') {
      // HOME_AWAY / ASIAN_HANDICAP: пари рядків по handicap.value
      // Групуємо home/away пари за абс. значенням handicap
      const groups = groupHandicapPairs(activeItems, participantsMap);
      for (const g of groups) {
        result.match_handicap.push({
          bookmaker   : bmName,
          scope       : scopeLabel,
          bettingType,
          handicap    : g.handicap,
          homeHcpOdd  : g.homeOdd,
          awayHcpOdd  : g.awayOdd,
          homeHcpOpen : g.homeOpen,
          awayHcpOpen : g.awayOpen,
        });
      }

    } else if (category === 'total' || category === 'home_ind_total' || category === 'away_ind_total') {
      // OVER_UNDER / HOME_OVER_UNDER / AWAY_OVER_UNDER
      // Групуємо OVER/UNDER пари за handicap.value (значення тоталу)
      const pairs = groupOverUnderPairs(activeItems);
      const targetArray =
        category === 'home_ind_total' ? result.home_ind_total :
        category === 'away_ind_total' ? result.away_ind_total :
        getScopeArray(result, bettingScope);

      for (const p of pairs) {
        targetArray.push({
          bookmaker  : bmName,
          scope      : scopeLabel,
          bettingType,
          line       : p.line,
          overOdd    : p.overOdd,
          underOdd   : p.underOdd,
          overOpen   : p.overOpen,
          underOpen  : p.underOpen,
        });
      }

    } else {
      // Інші типи — зберігаємо як є
      for (const item of activeItems) {
        result.other.push({
          bookmaker   : bmName,
          scope       : scopeLabel,
          bettingType,
          participantId: item.eventParticipantId,
          value       : item.value,
          opening     : item.opening,
          handicap    : item.handicap?.value ?? null,
          selection   : item.selection,
        });
      }
    }
  }

  // Видаляємо порожні масиви
  for (const key of Object.keys(result)) {
    if (Array.isArray(result[key]) && result[key].length === 0) delete result[key];
  }

  return result;
}

// ─── Допоміжні функції групування ────────────────────────────────────────────

/**
 * Групує рядки ASIAN_HANDICAP / HOME_AWAY по парах home+away
 * Повертає масив { handicap, homeOdd, awayOdd, homeOpen, awayOpen }
 * де handicap — значення фори для HOME (наприклад +6.5, -6.5 → повертаємо обидва як пару)
 */
function groupHandicapPairs(items, participantsMap) {
  // Індексуємо по абс. значенню handicap
  const byAbsHcp = {};
  for (const item of items) {
    const hcpVal = item.handicap?.value ?? null;
    if (hcpVal === null) continue;
    const absKey = String(Math.abs(parseFloat(hcpVal)));
    if (!byAbsHcp[absKey]) byAbsHcp[absKey] = [];
    byAbsHcp[absKey].push(item);
  }

  const pairs = [];
  for (const [, group] of Object.entries(byAbsHcp)) {
    // Розрізняємо home/away за eventParticipantId або знаком handicap
    let homeItem, awayItem;

    if (participantsMap?.homeId && participantsMap?.awayId) {
      homeItem = group.find(o => o.eventParticipantId === participantsMap.homeId);
      awayItem = group.find(o => o.eventParticipantId === participantsMap.awayId);
    }

    // Fallback: home = позитивний або перший
    if (!homeItem && !awayItem) {
      homeItem = group.find(o => parseFloat(o.handicap?.value ?? '0') >= 0);
      awayItem = group.find(o => parseFloat(o.handicap?.value ?? '0') < 0);
      if (!homeItem && group.length >= 2) { homeItem = group[0]; awayItem = group[1]; }
    }

    if (!homeItem || !awayItem) continue;

    pairs.push({
      handicap  : homeItem.handicap?.value ?? null,
      homeOdd   : homeItem.value,
      awayOdd   : awayItem.value,
      homeOpen  : homeItem.opening,
      awayOpen  : awayItem.opening,
    });
  }
  return pairs;
}

/**
 * Групує рядки OVER_UNDER по парах OVER+UNDER за однаковим handicap.value (= лінія тоталу)
 * Повертає масив { line, overOdd, underOdd, overOpen, underOpen }
 */
function groupOverUnderPairs(items) {
  const byLine = {};
  for (const item of items) {
    const line = item.handicap?.value ?? null;
    if (line === null) continue;
    if (!byLine[line]) byLine[line] = {};
    const sel = item.selection?.toUpperCase();
    if (sel === 'OVER')  { byLine[line].over  = item; }
    if (sel === 'UNDER') { byLine[line].under = item; }
  }

  const pairs = [];
  for (const [line, { over, under }] of Object.entries(byLine)) {
    if (!over && !under) continue;
    pairs.push({
      line      : parseFloat(line),
      overOdd   : over?.value   ?? null,
      underOdd  : under?.value  ?? null,
      overOpen  : over?.opening ?? null,
      underOpen : under?.opening ?? null,
    });
  }
  // Сортуємо по лінії
  pairs.sort((a, b) => a.line - b.line);
  return pairs;
}

/**
 * Повертає потрібний масив результату залежно від bettingScope для OVER_UNDER
 */
function getScopeArray(result, bettingScope) {
  if (/QUARTER/i.test(bettingScope)) return result.quarter_total;
  if (/HALF/i.test(bettingScope))    return result.half_total;
  return result.match_total;   // FULL_TIME / FULL_TIME_OVER_TIME
}

// ─── Витягуємо карту букмекерів з settings ───────────────────────────────────

function extractBookmakers(raw) {
  let data;
  try { data = JSON.parse(raw); } catch { return {}; }
  const root = data?.data?.findOddsByEventId ?? {};
  const map  = {};
  for (const bk of (root.settings?.bookmakers ?? [])) {
    map[bk.bookmaker.id] = bk.bookmaker.name;
  }
  return map;
}

// ─── Головна функція ──────────────────────────────────────────────────────────

/**
 * @param {string} matchId      — eventId (наприклад "jqZHZsIa")
 * @param {string} outputDir    — директорія для line_result.json
 * @param {object} [participants] — { homeId: "rJqHNXt2", awayId: "zDW2oyZ8" }
 *                                  (беруться з основного скрипту парсингу матчу)
 * @returns {Promise<object>}
 */
export async function fetchAndSaveLines(matchId, outputDir, participants = null) {
  const url = buildLinesUrl(matchId);
  console.log(`\n--- Завантаження ліній (findOddsByEventId)... ---`);
  console.log(`  URL: ${url}`);

  let raw = '';
  try {
    raw = await httpGetWithRetry(url);
  } catch (e) {
    console.warn(`⚠ line_result: помилка HTTP: ${e.message}`);
  }

  if (!raw || raw.length < 20) {
    console.warn('⚠ line_result: порожня відповідь');
    const empty = { error: 'empty_response', url, matchId };
    fs.mkdirSync(outputDir, { recursive: true });
    fs.writeFileSync(path.join(outputDir, 'line_result.json'), JSON.stringify(empty, null, 2), 'utf-8');
    return empty;
  }

  const bookmakersMap = extractBookmakers(raw);
  const parsed        = parseOddsResponse(raw, bookmakersMap, participants);

  if (!parsed) {
    const fallback = { error: 'parse_failed', matchId, url };
    fs.mkdirSync(outputDir, { recursive: true });
    fs.writeFileSync(path.join(outputDir, 'line_result.json'), JSON.stringify(fallback, null, 2), 'utf-8');
    return fallback;
  }

  // Службова мета
  if (!parsed._raw) {
    parsed.meta = {
      ...(parsed.meta ?? {}),
      matchId,
      url,
      fetchedAt : new Date().toISOString(),
    };
  }

  fs.mkdirSync(outputDir, { recursive: true });
  const outPath = path.join(outputDir, 'line_result.json');
  fs.writeFileSync(outPath, JSON.stringify(parsed, null, 2), 'utf-8');
  console.log(`✅ Лінії збережено: ${outPath}`);

  // Коротке summary
  const keys = [
    'match_1x2', 'match_handicap',
    'match_total', 'half_total', 'quarter_total',
    'home_ind_total', 'away_ind_total',
  ];
  for (const k of keys) {
    if (parsed[k]) console.log(`  ${k}: ${parsed[k].length} рядків`);
  }
  if (parsed.other?.length) console.log(`  other: ${parsed.other.length} рядків`);

  return parsed;
}