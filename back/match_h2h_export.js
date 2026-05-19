import fs from 'fs';
import path from 'path';
import { chromium } from 'playwright';
import XLSX from 'xlsx';
import readline from 'readline';
import { execSync } from 'child_process';
import { fileURLToPath } from 'url';
import https from 'https';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

try {
    console.log('>>> Очищення покинутих процесів браузера...');
    execSync('pkill -f chromium || true');
    execSync('pkill -f headless_shell || true');
} catch (e) {
}

import { OUTPUT_PATH } from './constants/index.js';

const MATCH_SELECTOR = '[id^="g_3_"]';
const LOAD_MORE_SELECTOR = '[data-testid="wcl-buttonLink"]';
const DEFAULT_LIMIT = 20;
const DEFAULT_H2H_LIMIT = 5;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function clearOutputDirectory() {
  if (fs.existsSync(OUTPUT_PATH)) {
    const files = fs.readdirSync(OUTPUT_PATH);
    for (const file of files) {
      const filePath = path.join(OUTPUT_PATH, file);
      try {
        if (fs.lstatSync(filePath).isFile()) {
          fs.unlinkSync(filePath);
        }
      } catch (err) {
        console.error(`Помилка при видаленні файлу ${file}:`, err.message);
      }
    }
    console.log('--- Папку outputs очищено (місце на VPS звільнено) ---');
  } else {
    fs.mkdirSync(OUTPUT_PATH, { recursive: true });
  }
}

function askQuestion(query) {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });
  return new Promise((resolve) =>
    rl.question(query, (ans) => {
      rl.close();
      resolve(ans);
    })
  );
}

function parseArgs() {
  const args = process.argv.slice(2);
  const options = {
    matchUrl: null,
    limit: DEFAULT_LIMIT,
    h2hLimit: DEFAULT_H2H_LIMIT,
    headless: true,
    fileType: 'xlsx',
    fileName: null,
    concurrency: 1,
  };

  for (const arg of args) {
    if (arg.startsWith('matchUrl=')) options.matchUrl = arg.slice('matchUrl='.length);
    if (arg.startsWith('limit=')) options.limit = Number(arg.slice('limit='.length)) || DEFAULT_LIMIT;
    if (arg.startsWith('h2hLimit=')) options.h2hLimit = Number(arg.slice('h2hLimit='.length)) || DEFAULT_H2H_LIMIT;
    if (arg.startsWith('fileType=')) options.fileType = arg.slice('fileType='.length).toLowerCase();
    if (arg.startsWith('fileName=')) options.fileName = arg.slice('fileName='.length);
    if (arg.startsWith('headless=')) options.headless = arg.slice('headless='.length) !== 'false';
    if (arg === '--no-headless') options.headless = false;
    if (arg === '--headless') options.headless = true;
  }
  options.concurrency = 1;

  if (!options.matchUrl) {
    throw new Error('❌ Missing required argument: matchUrl=<flashscore-match-url>');
  }

  return options;
}

function sanitizeFileName(value) {
  return String(value || 'flashscore_export')
    .normalize('NFKD')
    .replace(/[^\w\-. ]+/g, '')
    .replace(/\s+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 120) || 'flashscore_export';
}

function normalizeText(value) {
  return String(value || '')
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .replace(/[().,'’`]/g, '')
    .trim();
}

function extractMid(matchUrl) {
  const url = new URL(matchUrl);
  const mid = url.searchParams.get('mid');
  if (!mid) throw new Error('❌ Could not extract ?mid= from the provided matchUrl');
  return mid;
}

function buildMobileMatchUrl(matchId) {
  return `https://www.flashscore.mobi/match/${matchId}/`;
}

function buildTeamResultsUrl(teamUrl) {
  const url = new URL(teamUrl);
  const cleanPath = url.pathname.replace(/\/+$/, '');
  return `${url.origin}${cleanPath}/results/`;
}

function extractTeamId(teamUrl) {
  const parts = new URL(teamUrl).pathname.replace(/\/+$/, '').split('/').filter(Boolean);
  return parts[parts.length - 1] || null;
}

async function optimizePage(page) {
  await page.route('**/*', (route) => {
    const type = route.request().resourceType();
    if (['image', 'font', 'media', 'stylesheet'].includes(type) && !route.request().url().includes('results')) {
      route.abort();
    } else {
      route.continue();
    }
  });
}

async function safeGoto(page, url) {
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 90000 });
  await page.waitForTimeout(3000);
}

async function maybeAcceptCookies(page) {
  const selectors = [
    '#onetrust-accept-btn-handler',
    'button:has-text("Accept")',
    'button:has-text("Прийняти")',
    'button:has-text("I agree")',
    'button:has-text("Zgadzam się")',
  ];

  for (const selector of selectors) {
    try {
      const button = page.locator(selector).first();
      if (await button.isVisible({ timeout: 1500 }).catch(() => false)) {
        await button.click({ timeout: 2000 }).catch(() => {});
        await page.waitForTimeout(800);
        return;
      }
    } catch {}
  }
}

async function getBaseMatchInfo(context, matchUrl) {
  const matchId = extractMid(matchUrl);
  const mobileUrl = buildMobileMatchUrl(matchId);
  const page = await context.newPage();
  await optimizePage(page);

  try {
    await safeGoto(page, mobileUrl);
    const bodyText = await page.locator('body').innerText();
    const teamLinks = await page.$$eval('a[href*="/team/"]', (anchors) =>
      anchors.map((anchor) => ({
        name: anchor.textContent?.trim() || '',
        url: anchor.href,
      }))
    );

    if (teamLinks.length < 2) {
      throw new Error('❌ Could not extract team links');
    }

    const parsed = parseMobileMatchText(bodyText);
    const [homeTeamLink, awayTeamLink] = teamLinks;

    return {
      sourceMatchUrl: matchUrl,
      sourceMatchId: matchId,
      mobileUrl,
      ...parsed,
      home: {
        ...parsed.home,
        teamUrl: homeTeamLink.url,
        teamId: extractTeamId(homeTeamLink.url),
        resultsUrl: buildTeamResultsUrl(homeTeamLink.url),
      },
      away: {
        ...parsed.away,
        teamUrl: awayTeamLink.url,
        teamId: extractTeamId(awayTeamLink.url),
        resultsUrl: buildTeamResultsUrl(awayTeamLink.url),
      },
    };
  } finally {
    await page.close();
  }
}

function parseMobileMatchText(bodyText) {
  const lines = String(bodyText || '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);

  const titleLine = lines.find((line) => / - /.test(line)) || '';
  const titleIndex = lines.indexOf(titleLine);
  const tournament = titleIndex > 0 ? lines.slice(0, titleIndex).join(': ') : '';

  const scoreLine = lines.find((line) => /\d+:\d+/.test(line)) || '';
  const dateLine = lines.find((line) => /\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}/.test(line)) || '';

  const [homeName = '', awayName = ''] = titleLine.split(/\s+-\s+/);
  const scoreMatch = scoreLine.match(/(\d+):(\d+)(?:\s*\(([^)]*)\))?/);

  const periods = [];
  for (const line of lines) {
    const m = line.match(/^([\w\s]+?):\s*(\d+):(\d+)$/i);
    if (m) {
      periods.push({ label: m[1].trim(), home: Number(m[2]), away: Number(m[3]) });
    }
  }

  const quarterMap = {
    q1: null, q2: null, q3: null, q4: null, ot1: null, ot2: null, ot3: null,
  };

  let otIndex = 1;
  for (const period of periods) {
    const label = period.label.toLowerCase();
    if (label.includes('1st quarter')) quarterMap.q1 = period;
    else if (label.includes('2nd quarter')) quarterMap.q2 = period;
    else if (label.includes('3rd quarter')) quarterMap.q3 = period;
    else if (label.includes('4th quarter')) quarterMap.q4 = period;
    else if (label.includes('overtime')) {
      quarterMap[`ot${otIndex}`] = period;
      otIndex += 1;
    }
  }

  const statusLineIndex = scoreLine ? lines.indexOf(scoreLine) + 1 : -1;
  const status = statusLineIndex >= 0 ? lines[statusLineIndex] || '' : '';

  return {
    status,
    date: dateLine,
    tournament,
    home: { name: homeName },
    away: { name: awayName },
    result: {
      home: scoreMatch ? Number(scoreMatch[1]) : null,
      away: scoreMatch ? Number(scoreMatch[2]) : null,
      rawPeriods: scoreMatch?.[3] || '',
      total: scoreMatch ? Number(scoreMatch[1]) + Number(scoreMatch[2]) : null,
    },
    quarters: quarterMap,
  };
}

async function collectTeamRows(context, resultsUrl, limit, filterFn = null) {
  const page = await context.newPage();
  await page.route('**/*', (r) => ['image', 'media', 'font'].includes(r.request().resourceType()) ? r.abort() : r.continue());

  try {
    await safeGoto(page, resultsUrl);
    await maybeAcceptCookies(page);
    let emptyCycles = 0;
    let previousMatchedCount = 0;

    while (true) {
      const rows = await extractResultRows(page);
      const matchedRows = filterFn ? rows.filter(filterFn) : rows;
      
    
      if (matchedRows.length >= limit) break;

      const loadMoreBtn = page.locator(LOAD_MORE_SELECTOR).first();
      if (await loadMoreBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
        await loadMoreBtn.click().catch(() => {});
        await page.waitForTimeout(2000);
      } else {
        break;
      }

      if (matchedRows.length === previousMatchedCount) emptyCycles += 1;
      else emptyCycles = 0;
      previousMatchedCount = matchedRows.length;

      if (emptyCycles >= 4) break;
    }
    
    const finalRows = await extractResultRows(page);
    console.log(finalRows)
    const filtered = filterFn ? finalRows.filter(filterFn) : finalRows;
    return filtered.slice(0, limit);
  } finally {
    await page.close();
  }
}

async function extractResultRows(page) {
  await page.waitForSelector(MATCH_SELECTOR, { timeout: 15000 }).catch(() => {});
  return page.$$eval(MATCH_SELECTOR, (rows) =>
    rows.map((row) => {
      const anchor = row.querySelector('a.eventRowLink');
      const cellText = (s) => row.querySelector(s)?.textContent?.trim() || '';
      
      let tournament = '';
      let prev = row.previousElementSibling;
      while (prev) {
        if (prev.classList.contains('headerLeague__wrapper') || prev.classList.contains('event__header')) {
          tournament = prev.innerText.trim().replace(/\n/g, ': ');
          break;
        }
        prev = prev.previousElementSibling;
      }

      const hName = row.querySelector('.event__participant--home')?.textContent?.trim() || 
                    row.querySelector('.home')?.textContent?.trim() || 
                    row.querySelector('.event__homeParticipant')?.textContent?.trim() ||
                    row.querySelector('[data-testid="wcl-participant-home"]')?.textContent?.trim() ||
                    '';
      const aName = row.querySelector('.event__participant--away')?.textContent?.trim() || 
                    row.querySelector('.away')?.textContent?.trim() || 
                    row.querySelector('.event__awayParticipant')?.textContent?.trim() ||
                    row.querySelector('[data-testid="wcl-participant-away"]')?.textContent?.trim() ||
                    '';

      return {
        id: row.id?.replace(/^g_\d+_/, '') || '',
        sportId: row.id?.split('_')[1] || '3',
        url: anchor?.href || '',
        date: row.querySelector('.event__time')?.textContent?.trim() || '',
        timestamp: row.getAttribute('data-time') || '',
        tournament: tournament,
        homeName: hName,
        awayName: aName,
        homeScore: cellText('.event__score--home'),
        awayScore: cellText('.event__score--away'),
      };
    })
  );
}

function mapRowsWithSource(rows, sourceLabel) {
  return rows.map((row) => ({ ...row, sourceLabel }));
}

function isHeadToHeadRowFactory(opponentTeamId, opponentName) {
  return (row) => {
    const rowUrl = row.url || '';
    const byId = opponentTeamId ? rowUrl.includes(opponentTeamId) : false;
    const homeName = normalizeText(row.homeName);
    const awayName = normalizeText(row.awayName);
    const targetName = normalizeText(opponentName);
    return byId || (targetName && (homeName === targetName || awayName === targetName));
  };
}

/**
 * Отримує сирі дані (feed) від Flashscore через https.get
 */
async function fetchFlashscoreFeed(matchId, feedType, fsign) {
  const url = `https://35.flashscore.ninja/35/x/feed/${feedType}_${matchId}`;
  const options = {
    headers: {
      'x-fsign': fsign,
      'Referer': 'https://www.flashscore.ua/',
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
      'Accept': '*/*',
      'Origin': 'https://www.flashscore.ua'
    }
  };

  return new Promise((resolve, reject) => {
    https.get(url, options, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => resolve(data));
      res.on('error', reject);
    }).on('error', reject);
  });
}

/**
 * Парсить специфічний формат Flashscore (Key÷Value¬) в об'єкт
 */
function parseFlashscoreFields(text) {
  const obj = {};
  if (!text) return obj;
  const pairs = text.split('¬');
  for (const pair of pairs) {
    const [key, val] = pair.split('÷');
    if (key) obj[key] = val;
  }
  return obj;
}

function formatUnixDate(timestamp) {
  if (!timestamp) return '';
  const date = new Date(Number(timestamp) * 1000);
  return date.toLocaleString('ru-RU').replace(',', '');
}

/**
 * Отримує деталі матчу через dc, df_st та df_sur
 */
async function getMatchDetailsViaApi(matchId, fsign, sportId = '3') {
  const [surRaw, dcRaw, stRaw] = await Promise.all([
    fetchFlashscoreFeed(matchId, `df_sur_${sportId}`, fsign),
    fetchFlashscoreFeed(matchId, `dc_${sportId}`, fsign),
    fetchFlashscoreFeed(matchId, `df_st_${sportId}`, fsign)
  ]);

  const quarters = {
    q1: { home: '', away: '' }, q2: { home: '', away: '' },
    q3: { home: '', away: '' }, q4: { home: '', away: '' },
    ot1: { home: '', away: '' }
  };
  
  const surBlocks = surRaw.split('~');
  let homeScore = null;
  let awayScore = null;
  let status = '';
  let homeName = '';
  let awayName = '';
  let startTime = null;
  let tournament = '';

  for (const block of surBlocks) {
    const f = parseFlashscoreFields(block);
    if (f['ET']) tournament = (f['ER'] ? f['ER'] + ': ' : '') + f['ET'];
    if (f['BA']) quarters.q1 = { home: f['BA'], away: f['BB'] };
    if (f['BC']) quarters.q2 = { home: f['BC'], away: f['BD'] };
    if (f['BE']) quarters.q3 = { home: f['BE'], away: f['BF'] };
    if (f['BG']) quarters.q4 = { home: f['BG'], away: f['BH'] };
    if (f['BI']) quarters.ot1 = { home: f['BI'], away: f['BJ'] };
    if (f['AG']) homeScore = Number(f['AG']);
    if (f['AH']) awayScore = Number(f['AH']);
    if (f['DE']) status = f['DE'];
    if (f['AE']) homeName = f['AE'];
    if (f['AF']) awayName = f['AF'];
    if (f['AD']) startTime = f['AD'];
  }

  if (homeScore === null) {
    homeScore = Number(quarters.q1.home || 0) + Number(quarters.q2.home || 0) + 
                Number(quarters.q3.home || 0) + Number(quarters.q4.home || 0) + Number(quarters.ot1.home || 0);
    awayScore = Number(quarters.q1.away || 0) + Number(quarters.q2.away || 0) + 
                Number(quarters.q3.away || 0) + Number(quarters.q4.away || 0) + Number(quarters.ot1.away || 0);
  }

  // Об'єкт для збереження статистики з розбиттям по чвертях
  const stats = { Match: {}, Q1: {}, Q2: {}, Q3: {}, Q4: {}, OT1: {} };
  let currentPeriod = 'Match';

  const processStats = (raw) => {
    if (!raw) return;
    raw.split('~').slice(1).forEach(block => {
      const f = parseFlashscoreFields(block);
      
      // Визначаємо поточний період через поле SE
      if (f['SE']) {
        const se = f['SE'].toLowerCase();
        if (se.includes('матч') || se.includes('match') || se.includes('time')) {
          currentPeriod = 'Match';
        } else if (se.includes('1')) {
          currentPeriod = 'Q1';
        } else if (se.includes('2')) {
          currentPeriod = 'Q2';
        } else if (se.includes('3')) {
          currentPeriod = 'Q3';
        } else if (se.includes('4')) {
          currentPeriod = 'Q4';
        } else if (se.includes('овертайм') || se.includes('overtime') || se.includes('ot')) {
          currentPeriod = 'OT1';
        } else {
          currentPeriod = 'Match';
        }
      }

      // Зберігаємо статистику в об'єкт відповідного періоду
      if (f['SG']) {
        // У Flashscore зазвичай SH - це home, SI - away
        let hVal = f['SH'] !== undefined ? f['SH'] : f['SI'];
        let aVal = f['SH'] !== undefined ? f['SI'] : f['SJ'];
        
        // Зберігаємо значення
        stats[currentPeriod][f['SG']] = { home: hVal || "", away: aVal || "" };
      }
    });
  };

  processStats(dcRaw);
  processStats(stRaw);

  return {
    status,
    homeScore,
    awayScore,
    quarters,
    stats,
    homeName,
    awayName,
    startTime,
    tournament
  };
}

async function enrichMatchRows(context, rows, fsign = null) {
  const results = [];
  console.log(`--- Збагачення ${rows.length} матчів... ---`);
  for (const row of rows) {
    try {
      const matchId = row.id || extractMid(row.url);
      let details;
      if (fsign) {
        const apiData = await getMatchDetailsViaApi(matchId, fsign, row.sportId || '3');
        details = flattenMatchRecord({
          matchId,
          sourceLabel: row.sourceLabel,
          sourceRowUrl: row.url,
          sourceTournament: row.tournament,
          sourceHomeName: row.homeName,
          sourceAwayName: row.awayName,
          sourceDate: row.date || formatUnixDate(row.timestamp),
          status: apiData.status,
          result: { home: apiData.homeScore, away: apiData.awayScore },
          quarters: apiData.quarters,
          stats: apiData.stats,
          homeName: apiData.homeName,
          awayName: apiData.awayName,
          startTime: apiData.startTime
        });
      } else {
        details = await getMatchDetails(context, row);
      }
      results.push(details);
    } catch (error) {
      console.error(`Помилка для матчу ${row.id}:`, error.message);
      results.push({ ...row, error: error.message });
    }
    await sleep(fsign ? 50 : 400); 
  }
  return results;
}

async function getMatchDetails(context, row) {
  const page = await context.newPage();
  await optimizePage(page);
  const matchId = row.id || extractMid(row.url);
  const mobileUrl = buildMobileMatchUrl(matchId);
  try {
    await safeGoto(page, mobileUrl);
    const bodyText = await page.locator('body').innerText();
    const parsed = parseMobileMatchText(bodyText);
    return flattenMatchRecord({
      matchId, mobileUrl,
      sourceLabel: row.sourceLabel,
      sourceRowUrl: row.url,
      sourceDate: row.date,
      sourceTournament: row.tournament,
      sourceHomeName: row.homeName,
      sourceAwayName: row.awayName,
      ...parsed,
    });
  } finally {
    await page.close();
  }
}

function flattenMatchRecord(data) {
  const q = data.quarters || {};
  const s = data.stats || { Match: {}, Q1: {}, Q2: {}, Q3: {}, Q4: {}, OT1: {} };
  
  const val = (obj, prop) => (obj && obj[prop] !== undefined && obj[prop] !== null) ? obj[prop] : "";
  
  // Універсальні ключі для пошуку потрібної статистики різними мовами
  const statNames = {
    rebounds: ['rebounds', 'подборы', 'підбирання', 'rebound'],
    assists: ['assists', 'передачи', 'передачі', 'assist'],
    steals: ['steals', 'перехваты', 'перехоплення', 'steal'],
    blocks: ['blocks', 'блок-шоты', 'блок', 'block'],
    turnovers: ['turnovers', 'потери', 'втрати', 'turnover'],
    fouls: ['fouls', 'фолы', 'фоли', 'foul'],
    p2: ['2-point', '2-очковые', '2-очкові', '2-х', '2p'],
    p3: ['3-point', '3-очковые', '3-очкові', '3-х', '3p'],
    ft: ['free throws', 'штрафные', 'штрафні', 'free throw', 'ft']
  };

  // Розумна функція вилучення статів по періоду
  const getStat = (period, possibleNames, side) => {
    const periodStats = s[period] || {};
    for (const key of Object.keys(periodStats)) {
      const lowerKey = key.toLowerCase();
      // Шукаємо співпадіння за ключовими словами
      if (possibleNames.some(name => lowerKey.includes(name))) {
        return periodStats[key][side];
      }
    }
    return "";
  };

  const homeScore = data.result?.home ?? data.homeScore;
  const awayScore = data.result?.away ?? data.awayScore;
  const total = (homeScore !== null && awayScore !== null) ? (homeScore + awayScore) : null;
  const dateStr = data.startTime ? formatUnixDate(data.startTime) : (data.date || data.sourceDate || '');

  return {
    Source: data.sourceLabel || '',
    Match_ID: data.matchId || '',
    Date: dateStr,
    Status: data.status || '',
    Tournament: data.sourceTournament || '',
    Home_Team: data.homeName || data.home?.name || data.sourceHomeName || '',
    Away_Team: data.awayName || data.away?.name || data.sourceAwayName || '',
    Home_Score: homeScore,
    Away_Score: awayScore,
    Total_Score: total,
    
    // Scores by quarters
    Q1_Home: val(q.q1, 'home'), Q1_Away: val(q.q1, 'away'), Q1_Total: (q.q1 && q.q1.home !== "" && q.q1.away !== "") ? (Number(q.q1.home) + Number(q.q1.away)) : "",
    Q2_Home: val(q.q2, 'home'), Q2_Away: val(q.q2, 'away'), Q2_Total: (q.q2 && q.q2.home !== "" && q.q2.away !== "") ? (Number(q.q2.home) + Number(q.q2.away)) : "",
    Q3_Home: val(q.q3, 'home'), Q3_Away: val(q.q3, 'away'), Q3_Total: (q.q3 && q.q3.home !== "" && q.q3.away !== "") ? (Number(q.q3.home) + Number(q.q3.away)) : "",
    Q4_Home: val(q.q4, 'home'), Q4_Away: val(q.q4, 'away'), Q4_Total: (q.q4 && q.q4.home !== "" && q.q4.away !== "") ? (Number(q.q4.home) + Number(q.q4.away)) : "",
    OT_Home: val(q.ot1, 'home'), OT_Away: val(q.ot1, 'away'), OT_Total: (q.ot1 && q.ot1.home !== "" && q.ot1.away !== "") ? (Number(q.ot1.home) + Number(q.ot1.away)) : "",

    // --- GENERAL STATS (MATCH) ---
    Home_Rebounds: getStat('Match', statNames.rebounds, 'home'), Away_Rebounds: getStat('Match', statNames.rebounds, 'away'),
    Home_Assists: getStat('Match', statNames.assists, 'home'), Away_Assists: getStat('Match', statNames.assists, 'away'),
    Home_Steals: getStat('Match', statNames.steals, 'home'), Away_Steals: getStat('Match', statNames.steals, 'away'),
    Home_Blocks: getStat('Match', statNames.blocks, 'home'), Away_Blocks: getStat('Match', statNames.blocks, 'away'),
    Home_Turnovers: getStat('Match', statNames.turnovers, 'home'), Away_Turnovers: getStat('Match', statNames.turnovers, 'away'),
    Home_FreeThrows: getStat('Match', statNames.ft, 'home'), Away_FreeThrows: getStat('Match', statNames.ft, 'away'),
    
    // --- FOULS, 2-POINTERS, 3-POINTERS (MATCH) ---
    Home_Fouls_Match: getStat('Match', statNames.fouls, 'home'), Away_Fouls_Match: getStat('Match', statNames.fouls, 'away'),
    Home_2P_Match: getStat('Match', statNames.p2, 'home'), Away_2P_Match: getStat('Match', statNames.p2, 'away'),
    Home_3P_Match: getStat('Match', statNames.p3, 'home'), Away_3P_Match: getStat('Match', statNames.p3, 'away'),

    // --- FOULS, 2-POINTERS, 3-POINTERS (Q1) ---
    Home_Fouls_Q1: getStat('Q1', statNames.fouls, 'home'), Away_Fouls_Q1: getStat('Q1', statNames.fouls, 'away'),
    Home_2P_Q1: getStat('Q1', statNames.p2, 'home'), Away_2P_Q1: getStat('Q1', statNames.p2, 'away'),
    Home_3P_Q1: getStat('Q1', statNames.p3, 'home'), Away_3P_Q1: getStat('Q1', statNames.p3, 'away'),

    // --- FOULS, 2-POINTERS, 3-POINTERS (Q2) ---
    Home_Fouls_Q2: getStat('Q2', statNames.fouls, 'home'), Away_Fouls_Q2: getStat('Q2', statNames.fouls, 'away'),
    Home_2P_Q2: getStat('Q2', statNames.p2, 'home'), Away_2P_Q2: getStat('Q2', statNames.p2, 'away'),
    Home_3P_Q2: getStat('Q2', statNames.p3, 'home'), Away_3P_Q2: getStat('Q2', statNames.p3, 'away'),

    // --- FOULS, 2-POINTERS, 3-POINTERS (Q3) ---
    Home_Fouls_Q3: getStat('Q3', statNames.fouls, 'home'), Away_Fouls_Q3: getStat('Q3', statNames.fouls, 'away'),
    Home_2P_Q3: getStat('Q3', statNames.p2, 'home'), Away_2P_Q3: getStat('Q3', statNames.p2, 'away'),
    Home_3P_Q3: getStat('Q3', statNames.p3, 'home'), Away_3P_Q3: getStat('Q3', statNames.p3, 'away'),

    // --- FOULS, 2-POINTERS, 3-POINTERS (Q4) ---
    Home_Fouls_Q4: getStat('Q4', statNames.fouls, 'home'), Away_Fouls_Q4: getStat('Q4', statNames.fouls, 'away'),
    Home_2P_Q4: getStat('Q4', statNames.p2, 'home'), Away_2P_Q4: getStat('Q4', statNames.p2, 'away'),
    Home_3P_Q4: getStat('Q4', statNames.p3, 'home'), Away_3P_Q4: getStat('Q4', statNames.p3, 'away'),

    URL: data.sourceRowUrl || ''
  };
}
function ensureOutputDir() {
  fs.mkdirSync(OUTPUT_PATH, { recursive: true });
}

function autosizeWorksheet(worksheet, rows, headers) {
  const columns = headers || Object.keys(rows[0] || {});
  worksheet['!cols'] = columns.map((key) => {
    const maxLength = Math.max(key.length, ...rows.map((row) => String(row[key] ?? '').length));
    return { wch: Math.min(Math.max(maxLength + 2, 10), 40) };
  });
}

function writeJsonOutput(payload, fileName) {
  ensureOutputDir();
  const filePath = path.join(OUTPUT_PATH, `${fileName}.json`);
  fs.writeFileSync(filePath, JSON.stringify(payload, null, 2), 'utf-8');
  return filePath;
}

function writeXlsxOutput(payload, fileName) {
  ensureOutputDir();
  const filePath = path.join(OUTPUT_PATH, `${fileName}.xlsx`);
  const workbook = XLSX.utils.book_new();

  // Collect all unique keys for header
  const allKeys = new Set();
  payload.forEach(item => {
    Object.keys(item).forEach(key => allKeys.add(key));
  });
  const header = Array.from(allKeys);

  const worksheet = XLSX.utils.json_to_sheet(payload, { header });
  autosizeWorksheet(worksheet, payload, header);
  XLSX.utils.book_append_sheet(workbook, worksheet, 'Data');
  XLSX.writeFile(workbook, filePath);
  return filePath;
}

async function extractFsign(context, matchId) {
  const page = await context.newPage();
  let fsign = '';
  
  page.on('request', req => {
    const headers = req.headers();
    if (headers['x-fsign']) {
      fsign = headers['x-fsign'];
    }
  });

  try {
    const url = `https://www.flashscore.com/match/${matchId}/#/h2h/overall`;
    await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 });

    for (let i = 0; i < 20; i++) {
      if (fsign) break;
      await page.waitForTimeout(500);
    }
  } catch (e) {
    console.error('Ошибка при получении fsign:', e.message);
  } finally {
    await page.close();
  }
  return fsign;
}

async function main() {
  await clearOutputDirectory()
  const options = parseArgs();
  const userChoice = await askQuestion('Export to: (1) Excel or (2) JSON? [Enter 1 or 2]: ');
  options.fileType = userChoice.trim() === '2' ? 'json' : 'xlsx';

  const chromePath = fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined;

  const browser = await chromium.launch({ 
    headless: true,
    executablePath: chromePath,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
  });

  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
  });

  try {
    console.log('--- Processing main match ---');
    const baseMatch = await getBaseMatchInfo(context, options.matchUrl);
    console.log(`Teams: ${baseMatch.home.name} vs ${baseMatch.away.name}`);

    console.log('--- Отримання fsign для API... ---');
    const fsign = await extractFsign(context, baseMatch.sourceMatchId);
    console.log(`Captured fsign: ${fsign}`);

    const homeRows = await collectTeamRows(context, baseMatch.home.resultsUrl, options.limit);
    const awayRows = await collectTeamRows(context, baseMatch.away.resultsUrl, options.limit);
    const h2hRows = await collectTeamRows(context, baseMatch.home.resultsUrl, options.h2hLimit, isHeadToHeadRowFactory(baseMatch.away.teamId, baseMatch.away.name));

    const homeData = await enrichMatchRows(context, mapRowsWithSource(homeRows, `${baseMatch.home.name} (Recent)`), fsign);
    const awayData = await enrichMatchRows(context, mapRowsWithSource(awayRows, `${baseMatch.away.name} (Recent)`), fsign);
    const h2hData = await enrichMatchRows(context, mapRowsWithSource(h2hRows, 'H2H'), fsign);

    // Enrich main match too
    let sourceMatchDetails = {};
    if (fsign) {
        const mainMatchApiData = await getMatchDetailsViaApi(baseMatch.sourceMatchId, fsign);
        sourceMatchDetails = {
            status: mainMatchApiData.status,
            result: { home: mainMatchApiData.homeScore, away: mainMatchApiData.awayScore },
            quarters: mainMatchApiData.quarters,
            stats: mainMatchApiData.stats,
            homeName: mainMatchApiData.homeName,
            awayName: mainMatchApiData.awayName,
            startTime: mainMatchApiData.startTime,
            tournament: mainMatchApiData.tournament
        };
    }

    const sourceMatch = flattenMatchRecord({
      matchId: baseMatch.sourceMatchId,
      mobileUrl: baseMatch.mobileUrl,
      sourceLabel: 'MAIN MATCH',
      sourceRowUrl: options.matchUrl,
      ...baseMatch,
      ...sourceMatchDetails
    });

    const separator = (label) => ({ Source: `--- ${label.toUpperCase()} ---` });

    const payload = [
      separator('Main Match'),
      sourceMatch,
      { Source: '' }, 
      separator(`${baseMatch.home.name} Last ${options.limit}`),
      ...homeData,
      { Source: '' },
      separator(`${baseMatch.away.name} Last ${options.limit}`),
      ...awayData,
      { Source: '' }, 
      separator('Head to Head'),
      ...h2hData,
    ];

    const baseFileName = sanitizeFileName(options.fileName || `${baseMatch.home.name}_vs_${baseMatch.away.name}`);
    const outputFile = options.fileType === 'json' ? writeJsonOutput(payload, baseFileName) : writeXlsxOutput(payload, baseFileName);
    const now = new Date().toLocaleString('ru-RU');
    const logEntry = `[${now}] ${outputFile}\n`;
    fs.appendFileSync(path.join(__dirname, "log.txt"), logEntry)
    console.log(`\nSuccess! File saved: ${outputFile}`);
  } finally {
    await browser.close();
    console.log('--- Process finished ---');
    process.exit(0);
  }
}

main().catch(e => console.error(e));
