import { chromium } from 'playwright';
import fs from 'fs';
import zlib from 'zlib';
import https from 'https';

// ── Config ────────────────────────────────────────────────────────────────────
const FEED_URL = 'https://35.flashscore.ninja/35/x/feed/f_3_0_3_ua_5';
const OUTPUT_FILE = process.env.MATCHES_FILE || 'src/matches.json';

// ── Extract fsign via headless browser ───────────────────────────────────────
async function extractFsign() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();

  let fsign = '';

  page.on('request', req => {
    const h = req.headers();
    if (h['x-fsign'] && !fsign) {
      fsign = h['x-fsign'];
    }
  });

  try {
    await page.goto('https://www.flashscore.ua/', {
      waitUntil: 'networkidle',
      timeout: 60000,
    });

    // Wait until fsign captured or timeout
    for (let i = 0; i < 40 && !fsign; i++) {
      await page.waitForTimeout(500);
    }
  } catch (e) {
    console.error('fsign extraction error:', e.message);
  } finally {
    await page.close();
    await browser.close();
  }

  if (!fsign) throw new Error('Failed to extract fsign');
  console.log(`fsign: ${fsign}`);
  return fsign;
}

// ── Fetch raw feed ────────────────────────────────────────────────────────────
function fetchFeed(fsign) {
  return new Promise((resolve, reject) => {
    const options = {
      headers: {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'br',
        'Referer': 'https://www.flashscore.ua/',
        'X-GeoIP': '1',
        'x-fsign': fsign,
        'Origin': 'https://www.flashscore.ua',
        'DNT': '1',
      },
    };

    https.get(FEED_URL, options, (res) => {
      const chunks = [];
      res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => {
        const buf = Buffer.concat(chunks);
        zlib.brotliDecompress(buf, (err, decoded) => {
          if (err) reject(err);
          else resolve(decoded.toString('utf8'));
        });
      });
    }).on('error', reject);
  });
}

// ── Parse raw feed ────────────────────────────────────────────────────────────
//
// Record separator: ~
// Each record is key÷value pairs separated by ¬
//
// Key reference:
//   AA÷  match ID
//   AD÷  unix timestamp (scheduled kickoff)
//   AB÷  status: 1=scheduled, 2=live, 3=finished
//   AC÷  live status code (12=1st half, 13=2nd half, etc.)
//   AO÷  last update unix timestamp (live/finished)
//   CX÷  home team
//   AF÷  away team (within same record, after WN÷ separator block)
//   AG÷  home score
//   AH÷  away score
//
// Live status codes (AC):
//   1  = not started
//   6  = 1st half
//   7  = half time
//   8  = 2nd half
//   9  = extra time
//   10 = penalties
//   11 = after extra time
//   12 = live (generic, used for some sports)
//   13 = live (generic variant)

const LIVE_STATUS_MAP = {
  '6':  '1st Half',
  '7':  'Half Time',
  '8':  '2nd Half',
  '9':  'Extra Time',
  '10': 'Penalties',
  '11': 'After ET',
  '12': 'Live',
  '13': 'Live',
};

const AB_STATUS_MAP = {
  '1': 'scheduled',
  '2': 'live',
  '3': 'finished',
};

function parseField(record, key) {
  // Match key÷value up to next ¬ or end
  const re = new RegExp(`${key}÷([^¬]*)`, '');
  const m = record.match(re);
  return m ? m[1] : null;
}

function parseFeed(raw) {
  // Split into records by ~
  const records = raw.split('~');
  const matches = [];

  let currentLeague = '';

  for (const record of records) {
    if (!record.trim()) continue;

    // League header records start with ZA÷
    if (record.includes('ZA÷') && !record.includes('AA÷')) {
      const za = parseField(record, 'ZA');
      if (za) currentLeague = za;
      continue;
    }

    // Match records contain AA÷ (match ID)
    const matchId = parseField(record, 'AA');
    if (!matchId) continue;

    const timestamp = parseField(record, 'AD');
    const abStatus  = parseField(record, 'AB');
    const acCode    = parseField(record, 'AC');
    const homeTeam  = parseField(record, 'CX');
    const awayTeam  = parseField(record, 'AF');
    const homeScore = parseField(record, 'AG');
    const awayScore = parseField(record, 'AH');

    const status = AB_STATUS_MAP[abStatus] ?? `unknown(${abStatus})`;

    const ts = timestamp ? parseInt(timestamp, 10) : null;
    const kickoffDate = ts ? new Date(ts * 1000) : null;

    const match = {
      id:        matchId,
      league:    currentLeague,
      status,
      timestamp: ts,
      kickoff:   kickoffDate ? kickoffDate.toISOString() : null,
      kickoffLocal: kickoffDate
        ? kickoffDate.toLocaleString('uk-UA', {
            timeZone: 'Europe/Kyiv',
            day:    '2-digit',
            month:  '2-digit',
            year:   'numeric',
            hour:   '2-digit',
            minute: '2-digit',
          })
        : null,
      home:      homeTeam ?? '',
      away:      awayTeam ?? '',
    };

    if (status === 'live') {
      match.liveStatus = LIVE_STATUS_MAP[acCode] ?? `code(${acCode})`;
      match.score = {
        home: homeScore !== null ? parseInt(homeScore, 10) : null,
        away: awayScore !== null ? parseInt(awayScore, 10) : null,
      };
    }

    if (status === 'finished') {
      match.score = {
        home: homeScore !== null ? parseInt(homeScore, 10) : null,
        away: awayScore !== null ? parseInt(awayScore, 10) : null,
      };
    }

    matches.push(match);
  }

  return matches;
}

// ── Main ──────────────────────────────────────────────────────────────────────
(async () => {
  console.log('Extracting fsign...');
  const fsign = await extractFsign();

  console.log('Fetching feed...');
  const raw = await fetchFeed(fsign);

  console.log('Parsing feed...');
  const matches = parseFeed(raw);

  const out = {
    fetchedAt: new Date().toISOString(),
    fsign,
    count: matches.length,
    matches,
  };

  // Overwrite (not append)
  fs.writeFileSync(OUTPUT_FILE, JSON.stringify(out, null, 2), 'utf8');
  console.log(`Written ${matches.length} matches to ${OUTPUT_FILE}`);

  // Quick summary
  const byStatus = matches.reduce((acc, m) => {
    acc[m.status] = (acc[m.status] ?? 0) + 1;
    return acc;
  }, {});
  console.log('Summary:', byStatus);
})();