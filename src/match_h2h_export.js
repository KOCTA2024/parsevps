import fs from 'fs';
import path from 'path';
import { chromium } from 'playwright';
import XLSX from 'xlsx';
import readline from 'readline';
import { execSync } from 'child_process';
import { fileURLToPath } from 'url';
import https from 'https';
import zlib from 'zlib';
import { fetchAndSaveLines } from './parse_lines.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// ─── Kill leftover browser processes + clean tmp ──────────────────────────────
try { execSync('pkill -f chromium || true'); } catch {}
try { execSync('pkill -f headless_shell || true'); } catch {}
try { execSync('pkill -f chrome || true'); } catch {}
try { execSync('sleep 1'); } catch {}
try { execSync('rm -rf /tmp/.org.chromium.* /tmp/playwright* 2>/dev/null || true'); } catch {}

import { OUTPUT_PATH } from './constants/index.js';

const DEFAULT_LIMIT     = 35;
const DEFAULT_H2H_LIMIT = 5;

// ─── utils ────────────────────────────────────────────────────────────────────

function clearOutputDirectory() {
  if (fs.existsSync(OUTPUT_PATH)) {
    for (const file of fs.readdirSync(OUTPUT_PATH)) {
      try { fs.unlinkSync(path.join(OUTPUT_PATH, file)); } catch {}
    }
    console.log('--- Папку outputs очищено ---');
  } else {
    fs.mkdirSync(OUTPUT_PATH, { recursive: true });
  }
}

function askQuestion(query) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise(resolve => rl.question(query, ans => { rl.close(); resolve(ans); }));
}

function parseArgs() {
  const args = process.argv.slice(2);
  const options = {
    matchUrl : null,
    limit    : DEFAULT_LIMIT,
    h2hLimit : DEFAULT_H2H_LIMIT,
    fileType : 'xlsx',
    fileName : null,
  };
  for (const arg of args) {
    if (arg.startsWith('matchUrl='))  options.matchUrl  = arg.slice('matchUrl='.length);
    if (arg.startsWith('limit='))     options.limit     = Number(arg.slice('limit='.length))    || DEFAULT_LIMIT;
    if (arg.startsWith('h2hLimit=')) options.h2hLimit  = Number(arg.slice('h2hLimit='.length)) || DEFAULT_H2H_LIMIT;
    if (arg.startsWith('fileType=')) options.fileType  = arg.slice('fileType='.length).toLowerCase();
    if (arg.startsWith('fileName=')) options.fileName  = arg.slice('fileName='.length);
  }
  if (!options.matchUrl) throw new Error('❌ Missing required argument: matchUrl=<flashscore-url>');
  return options;
}

function sanitizeFileName(value) {
  return String(value || 'flashscore_export')
    .normalize('NFKD').replace(/[^\w\-. ]+/g, '').replace(/\s+/g, '_')
    .replace(/_+/g, '_').replace(/^_+|_+$/g, '').slice(0, 120) || 'flashscore_export';
}

function extractMid(matchUrl) {
  const mid = new URL(matchUrl).searchParams.get('mid');
  if (!mid) throw new Error('❌ Could not extract ?mid= from the provided matchUrl');
  return mid;
}

function formatUnixDate(ts) {
  if (!ts) return '';
  return new Date(Number(ts) * 1000).toLocaleString('ru-RU').replace(',', '');
}

// ─── Status mapping from DI field (dc_ feed) ─────────────────────────────────

function parseDcStatus(raw, match) {
  if (!raw || raw.length < 3) return { statusStr: '', liveMinute: null };
  const kv = parseKV(raw.split('~')[0]);
  const di = kv['DI'];
  if (di === undefined) return { statusStr: '', liveMinute: null };
  const diNum = Number(di);
  if (diNum === -1) {
    const statusStr = (match && match.homeScore !== null && match.awayScore !== null)
      ? 'Finished'
      : 'Not Started';
    return { statusStr, liveMinute: null };
  }
  return { statusStr: 'Live', liveMinute: diNum };
}

// ─── API fetch (gzip-aware) ───────────────────────────────────────────────────

async function fetchFeed(feedName, fsign, prefix = '35') {
  const url = `https://${prefix}.flashscore.ninja/${prefix}/x/feed/${feedName}`;
  return new Promise((resolve, reject) => {
    https.get(url, {
      headers: {
        'x-fsign'        : fsign,
        'Referer'        : 'https://www.flashscore.ua/',
        'User-Agent'     : 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept'         : '*/*',
        'Accept-Encoding': 'gzip, deflate, br',
        'Origin'         : 'https://www.flashscore.ua',
      }
    }, res => {
      if (res.statusCode !== 200) console.error(`HTTP ${res.statusCode} for ${feedName}`);
      const enc = res.headers['content-encoding'];
      let stream = res;
      if (enc === 'gzip')         stream = res.pipe(zlib.createGunzip());
      else if (enc === 'deflate') stream = res.pipe(zlib.createInflate());
      else if (enc === 'br')      stream = res.pipe(zlib.createBrotliDecompress());
      const chunks = [];
      stream.on('data', c => chunks.push(c));
      stream.on('end',  () => resolve(Buffer.concat(chunks).toString('utf-8')));
      stream.on('error', reject);
    }).on('error', reject);
  });
}

async function fetchFeedSafe(feedName, fsign, prefix) {
  try { return await fetchFeed(feedName, fsign, prefix); }
  catch (e) { console.warn(`⚠ Could not fetch ${feedName}: ${e.message}`); return ''; }
}

// ─── KV parser ────────────────────────────────────────────────────────────────

function parseKV(text) {
  const obj = {};
  if (!text) return obj;
  for (const pair of text.split('¬')) {
    const sep = pair.indexOf('÷');
    if (sep > 0) obj[pair.slice(0, sep)] = pair.slice(sep + 1);
  }
  return obj;
}

// ─── H2H feed parser (df_hh_) ────────────────────────────────────────────────

function parseHhFeed(raw) {
  const result = { homeName: '', awayName: '', homeMatches: [], awayMatches: [], h2hMatches: [] };
  if (!raw) return result;

  const blocks = raw.split('~');
  let section = null;
  let sectionCount = 0;

  for (const block of blocks) {
    const f = parseKV(block);

    if ('KA' in f) {
      sectionCount = 0;
      if (result.h2hMatches.length > 0 || section === 'h2h') break;
      section = null;
      continue;
    }

    if ('KB' in f) {
      sectionCount++;
      const label = (f['KB'] || '').toLowerCase();
      if (label.includes('очні') || label.includes('head') || label.includes('h2h') || label.includes('direct')) {
        section = 'h2h';
      } else {
        if (sectionCount === 1) {
          section = 'home';
          const m = f['KB'].match(/:\s*(.+)$/) || [null, f['KB']];
          if (m) result.homeName = m[1].trim();
        } else if (sectionCount === 2) {
          section = 'away';
          const m = f['KB'].match(/:\s*(.+)$/) || [null, f['KB']];
          if (m) result.awayName = m[1].trim();
        }
      }
      continue;
    }

    if (!f['KP'] || !section) continue;

    const homeScore = f['KU'] !== undefined ? Number(f['KU']) : null;
    const awayScore = f['KT'] !== undefined ? Number(f['KT']) : null;

    const match = {
      matchId    : f['KP'],
      timestamp  : f['KC'] || null,
      date       : formatUnixDate(f['KC']),
      status     : '',
      tournament : (f['KF'] || '').trim(),
      country    : (f['KH'] || '').trim(),
      homeName   : (f['KJ'] || '').replace(/^\*/, '').trim(),
      awayName   : (f['KK'] || '').replace(/^\*/, '').trim(),
      homeTeamId : f['UQ'] || null,
      awayTeamId : f['UO'] || null,
      homeScore,
      awayScore,
      total      : (homeScore !== null && awayScore !== null) ? homeScore + awayScore : null,
      result     : f['KN'] || '',
      side       : f['KS'] || '',
      url        : `https://www.flashscore.com/match/${f['KP']}/`,
    };

    if (section === 'home')      result.homeMatches.push(match);
    else if (section === 'away') result.awayMatches.push(match);
    else if (section === 'h2h')  result.h2hMatches.push(match);
  }

  return result;
}

// ─── Quarter scores parser (df_sur_) ─────────────────────────────────────────

const PERIOD_KEY_PAIRS = [
  ['BA','BB'], ['BC','BD'], ['BE','BF'], ['BG','BH'],
  ['BI','BJ'], ['BK','BL'], ['BM','BN'], ['BO','BP'],
];

function parseSurFeed(raw) {
  const result = {};
  if (!raw || raw.length < 5) return result;

  const allKV = {};
  for (const block of raw.split('~')) Object.assign(allKV, parseKV(block));

  for (let i = 0; i < PERIOD_KEY_PAIRS.length; i++) {
    const [hKey, aKey] = PERIOD_KEY_PAIRS[i];
    if (allKV[hKey] === undefined && allKV[aKey] === undefined) continue;

    const label = i < 4 ? `Q${i + 1}` : `OT${i - 3}`;
    const home  = allKV[hKey] !== undefined ? allKV[hKey] : '';
    const away  = allKV[aKey] !== undefined ? allKV[aKey] : '';
    const total = (home !== '' && away !== '') ? Number(home) + Number(away) : '';

    result[`${label}_Home`]  = home;
    result[`${label}_Away`]  = away;
    result[`${label}_Total`] = total;
  }

  return result;
}

// ─── Statistics parser (df_st_) ───────────────────────────────────────────────

const SECTION_SUFFIX_MAP = {
  'матч'          : '_Match', 'match'         : '_Match',
  '1-а чверть'   : '_Q1',    '1st quarter'   : '_Q1', 'q1': '_Q1',
  '2-а чверть'   : '_Q2',    '2nd quarter'   : '_Q2', 'q2': '_Q2',
  '3-я чверть'   : '_Q3',    '3rd quarter'   : '_Q3', 'q3': '_Q3',
  '4-а чверть'   : '_Q4',    '4th quarter'   : '_Q4', 'q4': '_Q4',
  '1-й овертайм' : '_OT1',   '1st overtime'  : '_OT1',
  '2-й овертайм' : '_OT2',   '2nd overtime'  : '_OT2',
  '3-й овертайм' : '_OT3',   '3rd overtime'  : '_OT3',
};

const STAT_KEY_MAP = {
  'всього підбирань'           : 'Rebounds',
  'rebounds'                   : 'Rebounds',
  'підбирання'                 : 'Rebounds',
  'total rebounds'             : 'Rebounds',
  'підбирання у нападі'        : 'Off_Rebounds',
  'offensive rebounds'         : 'Off_Rebounds',
  'підбирання у захисті'       : 'Def_Rebounds',
  'defensive rebounds'         : 'Def_Rebounds',
  'передачі'                   : 'Assists',
  'assists'                    : 'Assists',
  'перехоплення'               : 'Steals',
  'steals'                     : 'Steals',
  'блоки'                      : 'Blocks',
  'blocks'                     : 'Blocks',
  'втрати'                     : 'Turnovers',
  'turnovers'                  : 'Turnovers',
  'персональні фоли'           : 'Fouls',
  'personal fouls'             : 'Fouls',
  'fouls'                      : 'Fouls',
  'спроби з гри'               : 'FGA',
  'field goal attempts'        : 'FGA',
  'влучання з гри'             : 'FGM',
  'field goals made'           : 'FGM',
  '% влучань з гри'            : 'FG_Pct',
  'field goal %'               : 'FG_Pct',
  '2-очкові спроби з гри'      : '2P_Att',
  '2-point field goal attempts': '2P_Att',
  '2-очкові влучання з гри'    : '2P_Made',
  '2-point field goals made'   : '2P_Made',
  '% 2-очкових влучань з гри'  : '2P_Pct',
  '2-point field goal %'       : '2P_Pct',
  '3-очкові спроби з гри'      : '3P_Att',
  '3-point field goal attempts': '3P_Att',
  '3-очкові влучання з гри'    : '3P_Made',
  '3-point field goals made'   : '3P_Made',
  '% 3-очкових влучань з гри'  : '3P_Pct',
  '3-point field goal %'       : '3P_Pct',
  'спроби з лінії штрафних'    : 'FT_Att',
  'free throw attempts'        : 'FT_Att',
  'влучання з лінії штрафних'  : 'FT_Made',
  'free throws made'           : 'FT_Made',
  '% влучань з лінії штрафних' : 'FT_Pct',
  'free throw %'               : 'FT_Pct',
};

function parseStFeed(raw) {
  const result = {};
  if (!raw || raw.length < 5) return result;

  let currentSuffix = '_Match';
  const unknownCounters = {};

  for (const block of raw.split('~')) {
    const f = parseKV(block);

    if ('SE' in f) {
      const sectionLabel = (f['SE'] || '').trim();
      const key = sectionLabel.toLowerCase().trim();
      currentSuffix = SECTION_SUFFIX_MAP[key] || null;
      if (!currentSuffix) {
        const numMatch = sectionLabel.match(/(\d+)/);
        const lower = sectionLabel.toLowerCase();
        if (lower.includes('overtime') || lower.includes('овертайм')) {
          currentSuffix = `_OT${numMatch ? numMatch[1] : '1'}`;
        } else if (lower.includes('чверть') || lower.includes('quarter')) {
          currentSuffix = `_Q${numMatch ? numMatch[1] : '?'}`;
        } else {
          currentSuffix = `_${sectionLabel.replace(/[^a-zA-Z0-9]+/g, '_').slice(0, 20)}`;
        }
      }
      continue;
    }

    if ('SF' in f) continue;

    if (!('SG' in f) || !currentSuffix) continue;

    const statLabel = (f['SG'] || '').trim();
    const homeVal   = f['SH'] !== undefined ? f['SH'].trim() : '';
    const awayVal   = f['SI'] !== undefined ? f['SI'].trim() : '';

    const fieldBase = STAT_KEY_MAP[statLabel.toLowerCase()];

    if (fieldBase) {
      result[`Home_${fieldBase}${currentSuffix}`] = homeVal;
      result[`Away_${fieldBase}${currentSuffix}`] = awayVal;
    } else {
      const safeLabel = statLabel.replace(/[^a-zA-Zа-яА-ЯёЁіІїЇєЄ0-9]+/g, '_').slice(0, 28);
      const counterKey = `${safeLabel}${currentSuffix}`;
      unknownCounters[counterKey] = (unknownCounters[counterKey] || 0) + 1;
      const dedup = unknownCounters[counterKey] > 1 ? `_${unknownCounters[counterKey]}` : '';
      result[`Home_${safeLabel}${currentSuffix}${dedup}`] = homeVal;
      result[`Away_${safeLabel}${currentSuffix}${dedup}`] = awayVal;
    }
  }

  return result;
}

function transliterate(str) {
  const map = {
    'а':'a','б':'b','в':'v','г':'g','ґ':'g','д':'d','е':'e','є':'ie','ж':'zh',
    'з':'z','и':'y','і':'i','ї':'i','й':'j','к':'k','л':'l','м':'m','н':'n',
    'о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'kh','ц':'ts',
    'ч':'ch','ш':'sh','щ':'shch','ь':'','ю':'yu','я':'ya',
    'А':'A','Б':'B','В':'V','Г':'G','Ґ':'G','Д':'D','Е':'E','Є':'Ie','Ж':'Zh',
    'З':'Z','И':'Y','І':'I','Ї':'I','Й':'J','К':'K','Л':'L','М':'M','Н':'N',
    'О':'O','П':'P','Р':'R','С':'S','Т':'T','У':'U','Ф':'F','Х':'Kh','Ц':'Ts',
    'Ч':'Ch','Ш':'Sh','Щ':'Shch','Ь':'','Ю':'Yu','Я':'Ya',
  };
  return str.split('').map(c => map[c] ?? c).join('');
}

// ─── Live score helpers ───────────────────────────────────────────────────────
const QUARTER_LABELS = ['Q1', 'Q2', 'Q3', 'Q4'];

function calcLiveScores(qd) {
  let homeSum = 0, awaySum = 0, anyPresent = false;
  for (const label of [...QUARTER_LABELS, 'OT1','OT2','OT3','OT4','OT5','OT6']) {
    const h = qd[`${label}_Home`];
    const a = qd[`${label}_Away`];
    if (h !== '' && h !== undefined && a !== '' && a !== undefined) {
      homeSum += Number(h); awaySum += Number(a); anyPresent = true;
    }
  }
  if (!anyPresent) return { liveHome: '', liveAway: '', liveTotal: '' };
  return { liveHome: homeSum, liveAway: awaySum, liveTotal: homeSum + awaySum };
}

function detectBreakLabel(qd, isLive, periodName) {
  if (!isLive || !periodName) return '';
  
  // Проверяем, передан ли вообще статус перерыва из HTML главного матча
  const hasBreakWord = /перерва|half.?time|break|interval/i.test(periodName);
  if (!hasBreakWord) return '';

  let lastFilled = -1;
  for (let i = 0; i < QUARTER_LABELS.length; i++) {
    const h = qd[`${QUARTER_LABELS[i]}_Home`];
    const a = qd[`${QUARTER_LABELS[i]}_Away`];
    if (h !== '' && h !== undefined && a !== '' && a !== undefined) lastFilled = i;
  }

  if (lastFilled >= 0 && lastFilled < 3) {
    return `Перерва (після ${QUARTER_LABELS[lastFilled]})`;
  }
  return 'Перерва';
}

// ─── Build one flat match row ─────────────────────────────────────────────────

function buildMatchRow(match, sourceLabel, quarterData, statsData, status, periodName, seriesInfo) {
  const qd = quarterData || {};
  const sd = statsData   || {};
  const g  = (key, fallback = '') => sd[key] !== undefined ? sd[key] : fallback;

  const isLive = typeof status === 'string' && status.toLowerCase().startsWith('live');
  const { liveHome, liveAway, liveTotal } = calcLiveScores(qd);
  const breakLabel = detectBreakLabel(qd, isLive, periodName || '');

  const displayHome   = isLive && liveHome  !== '' ? liveHome  : (match.homeScore !== null ? match.homeScore  : '');
  const displayAway   = isLive && liveAway  !== '' ? liveAway  : (match.awayScore !== null ? match.awayScore  : '');
  const displayTotal  = isLive && liveTotal !== '' ? liveTotal : (match.total     !== null ? match.total      : '');
  const baseStatus    = status !== undefined ? status : (match.status || '');
  const displayStatus = breakLabel || baseStatus;

  const baseTournament = match.tournament || '';
  const tournament = (seriesInfo && seriesInfo.trim())
    ? (baseTournament ? `${baseTournament} | ${seriesInfo.trim()}` : seriesInfo.trim())
    : baseTournament;

  return {
    src  : sourceLabel,
    mid  : match.matchId    || '',
    dt   : match.date       || '',
    st   : displayStatus,
    tour : tournament,
    ht   : match.homeName   || '',
    at   : match.awayName   || '',
    hs   : displayHome,
    as_  : displayAway,
    tot  : displayTotal,

    q1h : qd['Q1_Home']  ?? '', q1a : qd['Q1_Away']  ?? '', q1t : qd['Q1_Total']  ?? '',
    q2h : qd['Q2_Home']  ?? '', q2a : qd['Q2_Away']  ?? '', q2t : qd['Q2_Total']  ?? '',
    q3h : qd['Q3_Home']  ?? '', q3a : qd['Q3_Away']  ?? '', q3t : qd['Q3_Total']  ?? '',
    q4h : qd['Q4_Home']  ?? '', q4a : qd['Q4_Away']  ?? '', q4t : qd['Q4_Total']  ?? '',
    ot1h: qd['OT1_Home'] ?? '', ot1a: qd['OT1_Away'] ?? '', ot1t: qd['OT1_Total'] ?? '',
    ot2h: qd['OT2_Home'] ?? '', ot2a: qd['OT2_Away'] ?? '', ot2t: qd['OT2_Total'] ?? '',

    hfgam : g('Home_FGA_Match'),
    afgam : g('Away_FGA_Match'),
    hfgmm : g('Home_FGM_Match'),
    afgmm : g('Away_FGM_Match'),
    hfgpm : g('Home_FG_Pct_Match'),
    afgpm : g('Away_FG_Pct_Match'),
    h2pam : g('Home_2P_Att_Match'),
    a2pam : g('Away_2P_Att_Match'),
    h2pmm : g('Home_2P_Made_Match'),
    a2pmm : g('Away_2P_Made_Match'),
    h2ppm : g('Home_2P_Pct_Match'),
    a2ppm : g('Away_2P_Pct_Match'),
    h3pam : g('Home_3P_Att_Match'),
    a3pam : g('Away_3P_Att_Match'),
    h3pmm : g('Home_3P_Made_Match'),
    a3pmm : g('Away_3P_Made_Match'),
    h3ppm : g('Home_3P_Pct_Match'),
    a3ppm : g('Away_3P_Pct_Match'),
    hftam : g('Home_FT_Att_Match'),
    aftam : g('Away_FT_Att_Match'),
    hftmm : g('Home_FT_Made_Match'),
    aftmm : g('Away_FT_Made_Match'),
    hftpm : g('Home_FT_Pct_Match'),
    aftpm : g('Away_FT_Pct_Match'),

    hrbm  : g('Home_Rebounds_Match'),
    arbm  : g('Away_Rebounds_Match'),
    horbm : g('Home_Off_Rebounds_Match'),
    aorbm : g('Away_Off_Rebounds_Match'),
    hdrbm : g('Home_Def_Rebounds_Match'),
    adrbm : g('Away_Def_Rebounds_Match'),
    hastm : g('Home_Assists_Match'),
    aastm : g('Away_Assists_Match'),
    hstlm : g('Home_Steals_Match'),
    astlm : g('Away_Steals_Match'),
    hblkm : g('Home_Blocks_Match'),
    ablkm : g('Away_Blocks_Match'),
    htovm : g('Home_Turnovers_Match'),
    atovm : g('Away_Turnovers_Match'),
    hflsm : g('Home_Fouls_Match'),
    aflsm : g('Away_Fouls_Match'),

    hrb1 : g('Home_Rebounds_Q1'),    arb1 : g('Away_Rebounds_Q1'),
    horb1: g('Home_Off_Rebounds_Q1'), aorb1: g('Away_Off_Rebounds_Q1'),
    hdrb1: g('Home_Def_Rebounds_Q1'), adrb1: g('Away_Def_Rebounds_Q1'),
    hast1: g('Home_Assists_Q1'),      aast1: g('Away_Assists_Q1'),
    hstl1: g('Home_Steals_Q1'),       astl1: g('Away_Steals_Q1'),
    hblk1: g('Home_Blocks_Q1'),       ablk1: g('Away_Blocks_Q1'),
    htov1: g('Home_Turnovers_Q1'),    atov1: g('Away_Turnovers_Q1'),
    hfls1: g('Home_Fouls_Q1'),        afls1: g('Away_Fouls_Q1'),
    hfga1: g('Home_FGA_Q1'),          afga1: g('Away_FGA_Q1'),
    hfgm1: g('Home_FGM_Q1'),          afgm1: g('Away_FGM_Q1'),
    hfgp1: g('Home_FG_Pct_Q1'),       afgp1: g('Away_FG_Pct_Q1'),
    h2pa1: g('Home_2P_Att_Q1'),       a2pa1: g('Away_2P_Att_Q1'),
    h2pm1: g('Home_2P_Made_Q1'),      a2pm1: g('Away_2P_Made_Q1'),
    h2pp1: g('Home_2P_Pct_Q1'),       a2pp1: g('Away_2P_Pct_Q1'),
    h3pa1: g('Home_3P_Att_Q1'),       a3pa1: g('Away_3P_Att_Q1'),
    h3pm1: g('Home_3P_Made_Q1'),      a3pm1: g('Away_3P_Made_Q1'),
    h3pp1: g('Home_3P_Pct_Q1'),       a3pp1: g('Away_3P_Pct_Q1'),
    hfta1: g('Home_FT_Att_Q1'),       afta1: g('Away_FT_Att_Q1'),
    hftm1: g('Home_FT_Made_Q1'),      aftm1: g('Away_FT_Made_Q1'),
    hftp1: g('Home_FT_Pct_Q1'),       aftp1: g('Away_FT_Pct_Q1'),

    hrb2 : g('Home_Rebounds_Q2'),    arb2 : g('Away_Rebounds_Q2'),
    horb2: g('Home_Off_Rebounds_Q2'), aorb2: g('Away_Off_Rebounds_Q2'),
    hdrb2: g('Home_Def_Rebounds_Q2'), adrb2: g('Away_Def_Rebounds_Q2'),
    hast2: g('Home_Assists_Q2'),      aast2: g('Away_Assists_Q2'),
    hstl2: g('Home_Steals_Q2'),       astl2: g('Away_Steals_Q2'),
    hblk2: g('Home_Blocks_Q2'),       ablk2: g('Away_Blocks_Q2'),
    htov2: g('Home_Turnovers_Q2'),    atov2: g('Away_Turnovers_Q2'),
    hfls2: g('Home_Fouls_Q2'),        afls2: g('Away_Fouls_Q2'),
    hfga2: g('Home_FGA_Q2'),          afga2: g('Away_FGA_Q2'),
    hfgm2: g('Home_FGM_Q2'),          afgm2: g('Away_FGM_Q2'),
    hfgp2: g('Home_FG_Pct_Q2'),       afgp2: g('Away_FG_Pct_Q2'),
    h2pa2: g('Home_2P_Att_Q2'),       a2pa2: g('Away_2P_Att_Q2'),
    h2pm2: g('Home_2P_Made_Q2'),      a2pm2: g('Away_2P_Made_Q2'),
    h2pp2: g('Home_2P_Pct_Q2'),       a2pp2: g('Away_2P_Pct_Q2'),
    h3pa2: g('Home_3P_Att_Q2'),       a3pa2: g('Away_3P_Att_Q2'),
    h3pm2: g('Home_3P_Made_Q2'),      a3pm2: g('Away_3P_Made_Q2'),
    h3pp2: g('Home_3P_Pct_Q2'),       a3pp2: g('Away_3P_Pct_Q2'),
    hfta2: g('Home_FT_Att_Q2'),       afta2: g('Away_FT_Att_Q2'),
    hftm2: g('Home_FT_Made_Q2'),      aftm2: g('Away_FT_Made_Q2'),
    hftp2: g('Home_FT_Pct_Q2'),       aftp2: g('Away_FT_Pct_Q2'),

    hrb3 : g('Home_Rebounds_Q3'),    arb3 : g('Away_Rebounds_Q3'),
    horb3: g('Home_Off_Rebounds_Q3'), aorb3: g('Away_Off_Rebounds_Q3'),
    hdrb3: g('Home_Def_Rebounds_Q3'), adrb3: g('Away_Def_Rebounds_Q3'),
    hast3: g('Home_Assists_Q3'),      aast3: g('Away_Assists_Q3'),
    hstl3: g('Home_Steals_Q3'),       astl3: g('Away_Steals_Q3'),
    hblk3: g('Home_Blocks_Q3'),       ablk3: g('Away_Blocks_Q3'),
    htov3: g('Home_Turnovers_Q3'),    atov3: g('Away_Turnovers_Q3'),
    hfls3: g('Home_Fouls_Q3'),        afls3: g('Away_Fouls_Q3'),
    hfga3: g('Home_FGA_Q3'),          afga3: g('Away_FGA_Q3'),
    hfgm3: g('Home_FGM_Q3'),          afgm3: g('Away_FGM_Q3'),
    hfgp3: g('Home_FG_Pct_Q3'),       afgp3: g('Away_FG_Pct_Q3'),
    h2pa3: g('Home_2P_Att_Q3'),       a2pa3: g('Away_2P_Att_Q3'),
    h2pm3: g('Home_2P_Made_Q3'),      a2pm3: g('Away_2P_Made_Q3'),
    h2pp3: g('Home_2P_Pct_Q3'),       a2pp3: g('Away_2P_Pct_Q3'),
    h3pa3: g('Home_3P_Att_Q3'),       a3pa3: g('Away_3P_Att_Q3'),
    h3pm3: g('Home_3P_Made_Q3'),      a3pm3: g('Away_3P_Made_Q3'),
    h3pp3: g('Home_3P_Pct_Q3'),       a3pp3: g('Away_3P_Pct_Q3'),
    hfta3: g('Home_FT_Att_Q3'),       afta3: g('Away_FT_Att_Q3'),
    hftm3: g('Home_FT_Made_Q3'),      aftm3: g('Away_FT_Made_Q3'),
    hftp3: g('Home_FT_Pct_Q3'),       aftp3: g('Away_FT_Pct_Q3'),

    hrb4 : g('Home_Rebounds_Q4'),    arb4 : g('Away_Rebounds_Q4'),
    horb4: g('Home_Off_Rebounds_Q4'), aorb4: g('Away_Off_Rebounds_Q4'),
    hdrb4: g('Home_Def_Rebounds_Q4'), adrb4: g('Away_Def_Rebounds_Q4'),
    hast4: g('Home_Assists_Q4'),      aast4: g('Away_Assists_Q4'),
    hstl4: g('Home_Steals_Q4'),       astl4: g('Away_Steals_Q4'),
    hblk4: g('Home_Blocks_Q4'),       ablk4: g('Away_Blocks_Q4'),
    htov4: g('Home_Turnovers_Q4'),    atov4: g('Away_Turnovers_Q4'),
    hfls4: g('Home_Fouls_Q4'),        afls4: g('Away_Fouls_Q4'),
    hfga4: g('Home_FGA_Q4'),          afga4: g('Away_FGA_Q4'),
    hfgm4: g('Home_FGM_Q4'),          afgm4: g('Away_FGM_Q4'),
    hfgp4: g('Home_FG_Pct_Q4'),       afgp4: g('Away_FG_Pct_Q4'),
    h2pa4: g('Home_2P_Att_Q4'),       a2pa4: g('Away_2P_Att_Q4'),
    h2pm4: g('Home_2P_Made_Q4'),      a2pm4: g('Away_2P_Made_Q4'),
    h2pp4: g('Home_2P_Pct_Q4'),       a2pp4: g('Away_2P_Pct_Q4'),
    h3pa4: g('Home_3P_Att_Q4'),       a3pa4: g('Away_3P_Att_Q4'),
    h3pm4: g('Home_3P_Made_Q4'),      a3pm4: g('Away_3P_Made_Q4'),
    h3pp4: g('Home_3P_Pct_Q4'),       a3pp4: g('Away_3P_Pct_Q4'),
    hfta4: g('Home_FT_Att_Q4'),       afta4: g('Away_FT_Att_Q4'),
    hftm4: g('Home_FT_Made_Q4'),      aftm4: g('Away_FT_Made_Q4'),
    hftp4: g('Home_FT_Pct_Q4'),       aftp4: g('Away_FT_Pct_Q4'),

    url: match.url || '',
  };
}

// ─── Playwright: get fsign ────────────────────────────────────────────────────
// FIX: isolated context per call + stable DOM wait + double-read verification

async function extractFsign(browser, matchId) {
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
  });

  const page = await context.newPage();
  let fsign      = '';
  let prefix     = '35';
  let liveStatus = '';
  let seriesInfo = '';

  const fsignPromise = new Promise(resolve => {
    context.on('request', req => {
      const h = req.headers();
      if (h['x-fsign'] && req.url().includes('flashscore.ninja')) {
        const m = req.url().match(/https:\/\/(\d+)\.flashscore\.ninja/);
        resolve({
          fsign : h['x-fsign'],
          prefix: (m && m[1].length > 1) ? m[1] : '35',
        });
      }
    });
  });

  try {
    await page.goto(
      `https://www.flashscore.ua/match/${matchId}/#/h2h/overall`,
      { waitUntil: 'networkidle', timeout: 60000 }
    );

    const captured = await Promise.race([
      fsignPromise,
      new Promise((_, reject) => setTimeout(() => reject(new Error('fsign timeout')), 30000)),
    ]).catch(e => { console.warn('⚠ fsign capture:', e.message); return null; });

    if (captured) { fsign = captured.fsign; prefix = captured.prefix; }

    const liveWrapper = await page.$('.detailScore__wrapper.detailScore__live');
    if (liveWrapper) {
      await page.waitForFunction(() => {
        const el = document.querySelector('.fixedHeaderDuel__detailStatus');
        return el && el.textContent.trim().length > 0;
      }, { timeout: 8000 }).catch(() => {});

      let statusText = await page.$eval('.fixedHeaderDuel__detailStatus', el => el.textContent.trim()).catch(() => '');

      // Если в HTML написано "Перерва", парсим табло, чтобы узнать после какой чверти
      if (statusText.toLowerCase().includes('перерва')) {
        const lastQuarterNum = await page.$$eval('.smv__part', parts => {
          let validIndex = 0;
          parts.forEach((p, idx) => {
            if (p.textContent.trim() !== '') validIndex = idx + 1;
          });
          return validIndex;
        }).catch(() => 0);

        if (lastQuarterNum > 0 && lastQuarterNum < 5) {
          liveStatus = `Перерва (після Q${lastQuarterNum})`;
        } else {
          liveStatus = statusText;
        }
      } else {
        liveStatus = statusText; // Тут будет "1-а чверть", "2-а чверть" и т.д.
      }

      console.log(`  [Live Scraper] Статус из HTML: "${liveStatus}"`);
    }

    seriesInfo = await page.$eval('.infoBox__info', el => el.textContent.trim()).catch(() => '');
    if (seriesInfo) console.log(`  [Series Info] "${seriesInfo}"`);

  } catch (e) {
    console.error('fsign/live-parse error:', e.message);
  } finally {
    await page.close();
    await context.close();
  }

  return { fsign, prefix, liveStatus, seriesInfo };
}

// ─── Enrich matches with dc_ + sur + st feeds ─────────────────────────────────

const CONCURRENT_FETCH = 5;

async function enrichMatches(matches, sportId, fsign, prefix) {
  const enriched = new Map();
  for (let i = 0; i < matches.length; i += CONCURRENT_FETCH) {
    const batch = matches.slice(i, i + CONCURRENT_FETCH);
    await Promise.all(batch.map(async m => {
      const [rawDc, rawSur, rawSt, rawSt5] = await Promise.all([
        fetchFeedSafe(`dc_${sportId}_${m.matchId}`,     fsign, prefix),
        fetchFeedSafe(`df_sur_${sportId}_${m.matchId}`, fsign, prefix),
        fetchFeedSafe(`df_st_${sportId}_${m.matchId}`,  fsign, prefix),
        fetchFeedSafe(`df_st5_${m.matchId}`,            fsign, prefix),
      ]);
      const st5Data = parseStFeed(rawSt5);
      const stData  = parseStFeed(rawSt);
      const mergedStats = Object.assign({}, stData, st5Data);
      enriched.set(m.matchId, {
        dcStatus   : parseDcStatus(rawDc, m),
        quarterData: parseSurFeed(rawSur),
        statsData  : mergedStats,
      });
      console.log(`  ✓ ${m.homeName} vs ${m.awayName} (${m.matchId})`);
    }));
  }
  return enriched;
}

// ─── output ───────────────────────────────────────────────────────────────────

function ensureOutputDir() { fs.mkdirSync(OUTPUT_PATH, { recursive: true }); }

function writeXlsx(rows, fileName) {
  ensureOutputDir();
  const filePath = path.join(OUTPUT_PATH, `${fileName}.xlsx`);
  const wb = XLSX.utils.book_new();
  const headers = [...new Set(rows.flatMap(r => Object.keys(r)))];
  const ws = XLSX.utils.json_to_sheet(rows, { header: headers });
  ws['!cols'] = headers.map(k => ({
    wch: Math.min(Math.max(k.length, ...rows.map(r => String(r[k] ?? '').length)) + 2, 40)
  }));
  XLSX.utils.book_append_sheet(wb, ws, 'Data');
  XLSX.writeFile(wb, filePath);
  return filePath;
}

function writeJson(rows, fileName) {
  ensureOutputDir();
  const filePath = path.join(OUTPUT_PATH, `${fileName}.json`);
  fs.writeFileSync(filePath, JSON.stringify(rows, null, 2), 'utf-8');
  return filePath;
}

// ─── main ─────────────────────────────────────────────────────────────────────

async function main() {
  clearOutputDirectory();
  const options = parseArgs();
  const userChoice = await askQuestion('');
  options.fileType = userChoice.trim() === '2' ? 'json' : 'xlsx';

  const chromePath = fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined;

  // FIX: disable all caching at browser launch level
const browser = await chromium.launch({
    headless: true,
    executablePath: chromePath,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',                // <--- Возвращаем, без него на VPS без видеокарты хана
      '--no-zygote',                  // Отключает прожорливый процесс-инициализатор линукса
      '--single-process',             // Запускает браузер в ОДНОМ процессе (экономит до 150-200 МБ ОЗУ)
      '--disable-extensions',         // Никаких расширений
      '--blink-settings=imagesEnabled=false', // Намертво блокирует загрузку картинок на уровне движка
      '--disk-cache-size=0',
      '--disable-application-cache',
      '--disable-cache',
    ],
  });
  try {
    const matchId = extractMid(options.matchUrl);
    const sportId = options.matchUrl.includes('basketball') ? '5' : '1';

    console.log('--- Отримання fsign через Playwright... ---');

    // FIX: pass browser, not context — extractFsign creates its own isolated context
    const { fsign, prefix, liveStatus, seriesInfo } = await extractFsign(browser, matchId);
    console.log(`fsign: ${fsign}, prefix: ${prefix}`);
    if (!fsign) throw new Error('❌ Could not capture fsign');

    console.log('--- Завантаження df_hh_... ---');
    const rawHh = await fetchFeed(`df_hh_${sportId}_${matchId}`, fsign, prefix);
    if (!rawHh || rawHh.length < 50) throw new Error('❌ Empty df_hh_ response');

    let { homeName, awayName, homeMatches, awayMatches, h2hMatches } = parseHhFeed(rawHh);

    const homeSlice = homeMatches.slice(0, Math.min(options.limit,    homeMatches.length));
    const awaySlice = awayMatches.slice(0, Math.min(options.limit,    awayMatches.length));
    const h2hSlice  = h2hMatches.slice(0,  Math.min(options.h2hLimit, h2hMatches.length));

    console.log(`Teams: ${homeName} vs ${awayName}`);
    console.log(`Home: ${homeMatches.length}, Away: ${awayMatches.length}, H2H: ${h2hMatches.length}`);

    const mainMatchStub = { matchId, homeName, awayName };
    const allMatches = [mainMatchStub, ...homeSlice, ...awaySlice, ...h2hSlice];
    const uniqueMatches = [...new Map(allMatches.map(m => [m.matchId, m])).values()];

    console.log(`\n--- Збагачення ${uniqueMatches.length} матчів (dc_ + sur + st)... ---`);
    const enrichMap = await enrichMatches(uniqueMatches, sportId, fsign, prefix);

    const enrich = id => {
      const e = enrichMap.get(id);
      if (!e) return { status: '', quarterData: {}, statsData: {} };
      const { dcStatus, quarterData, statsData } = e;
      const { statusStr, liveMinute } = dcStatus || { statusStr: '', liveMinute: null };
      let status = statusStr;
      if (statusStr === 'Live' && liveMinute !== null) {
        status = `Live (${liveMinute}')`;
      }
      return { status, quarterData: quarterData || {}, statsData: statsData || {} };
    };

    let mainMatch = homeMatches.find(m => m.matchId === matchId)
      || awayMatches.find(m => m.matchId === matchId)
      || h2hMatches.find(m => m.matchId === matchId);

    if (!mainMatch) {
      mainMatch = {
        matchId,
        timestamp : null,
        date      : '',
        status    : '',
        tournament: '',
        homeName,
        awayName,
        homeScore : null,
        awayScore : null,
        total     : null,
        result    : '',
        side      : '',
        url       : `https://www.flashscore.com/match/${matchId}/`,
      };
    }

    // ── Лінії букмекерів — тут mainMatch вже визначено ──────────────────────
    const participants = {
      homeId: mainMatch.homeTeamId ?? homeMatches[0]?.homeTeamId ?? null,
      awayId: mainMatch.awayTeamId ?? awayMatches[0]?.awayTeamId ?? null,
    };
    console.log(`  participants: homeId=${participants.homeId}, awayId=${participants.awayId}`);
    await fetchAndSaveLines(matchId, OUTPUT_PATH, participants);
    // Копіюємо line_result.json → src/data/ щоб Python міг його читати
    const DATA_DIR = path.join(__dirname, 'data');
    fs.mkdirSync(DATA_DIR, { recursive: true });
    try {
      fs.copyFileSync(
        path.join(OUTPUT_PATH, 'line_result.json'),
        path.join(DATA_DIR, 'line_result.json')
      );
      console.log(`  ✅ line_result.json → ${DATA_DIR}`);
    } catch (e) {
      console.warn(`  ⚠ Не вдалося скопіювати line_result.json: ${e.message}`);
    }

    if (!homeName || !awayName) {
      if (mainMatch.homeName) homeName = mainMatch.homeName;
      if (mainMatch.awayName) awayName = mainMatch.awayName;
    }
    if (!homeName || !awayName) {
      const ref = h2hSlice[0] || homeSlice[0] || awaySlice[0];
      if (ref) {
        if (!homeName) homeName = ref.homeName;
        if (!awayName) awayName = ref.awayName;
      }
    }
    console.log(`Resolved names for file: ${homeName} vs ${awayName}`);

    const { dcStatus: dcMainStatus, quarterData: mainQ, statsData: mainS } =
      enrichMap.get(matchId) || { dcStatus: { statusStr: '', liveMinute: null }, quarterData: {}, statsData: {} };
    const { statusStr: dcStatusStr, liveMinute: dcMinute } =
      dcMainStatus || { statusStr: '', liveMinute: null };

    let mainStatus = '';
    if (liveStatus) {
      const isBreak = /перерва|half.?time|break|interval/i.test(liveStatus);
      if (isBreak) {
        mainStatus = `Live (${liveStatus})`;
      } else if (dcMinute !== null) {
        mainStatus = `Live (${liveStatus} ${dcMinute}')`;
      } else {
        mainStatus = `Live (${liveStatus})`;
      }
    } else if (dcStatusStr) {
      mainStatus = dcStatusStr === 'Live' && dcMinute !== null
        ? `Live (${dcMinute}')`
        : dcStatusStr;
    }

    const sep   = label => ({ Source: `--- ${label.toUpperCase()} ---` });
    const blank = ()    => ({ Source: '' });

    const payload = [
      sep('Main Match'),
      buildMatchRow(mainMatch, 'MAIN MATCH', mainQ, mainS, mainStatus, liveStatus, seriesInfo),
      blank(),
      sep(`${homeName} Last ${homeSlice.length}`),
      ...homeSlice.map(m => {
        const { status, quarterData, statsData } = enrich(m.matchId);
        return buildMatchRow(m, `${homeName} (Recent)`, quarterData, statsData, status);
      }),
      blank(),
      sep(`${awayName} Last ${awaySlice.length}`),
      ...awaySlice.map(m => {
        const { status, quarterData, statsData } = enrich(m.matchId);
        return buildMatchRow(m, `${awayName} (Recent)`, quarterData, statsData, status);
      }),
      blank(),
      sep('Head to Head'),
      ...h2hSlice.map(m => {
        const { status, quarterData, statsData } = enrich(m.matchId);
        return buildMatchRow(m, 'H2H', quarterData, statsData, status);
      }),
    ];

    const rawName  = options.fileName || `${transliterate(homeName)}_vs_${transliterate(awayName)}`;
    const baseName = sanitizeFileName(rawName);
    const outputFile = options.fileType === 'json'
      ? writeJson(payload, baseName)
      : writeXlsx(payload, baseName);

    // Зберігаємо payload як JSON у src/data/ для Python
    const payloadPath = path.join(DATA_DIR, `${baseName}.json`);
    fs.writeFileSync(payloadPath, JSON.stringify(payload, null, 2), 'utf-8');
    console.log(`✅ Data для Python: ${payloadPath}`);
    fs.appendFileSync(
      path.join(__dirname, 'log.txt'),
      `[${new Date().toLocaleString('ru-RU')}] ${outputFile}\n`
    );
    console.log(`\n✅ Success! File saved: ${outputFile}`);

  } finally {
    await browser.close();
    console.log('--- Process finished ---');
    process.exit(0);
  }
}

main().catch(e => { console.error(e); process.exit(1); });