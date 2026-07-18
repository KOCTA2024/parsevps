'use strict';

/**
 * match_stage.js
 *
 * Легка перевірка поточної стадії матчу — БЕЗ повного парсингу, який виконує
 * src/match_h2h_export.js (без df_hh, без архівів сезонів, без h2h/статистики).
 * Призначення: дати відповідь "матч зараз у перерві чи ні" якомога дешевше,
 * щоб можна було опитувати десятки матчів раз на хвилину, не кладучи ВПС.
 *
 * Механіка — та сама, що й в основному парсері:
 *   1. Playwright відкриває сторінку матчу й перехоплює заголовок x-fsign
 *      з мережевого запиту до flashscore.ninja (це "токен" для прямих
 *      HTTP-запитів до фідів, без подальшого рендерингу сторінки).
 *   2. Стадія матчу читається з фіда dc_<sportId>_<matchId> за полями DA/DI:
 *        DA=1  → матч ще не почався
 *        DA=2  → матч живий
 *        DA=3  → матч завершено
 *        DI=-1 → (при DA=2) годинник зупинено = ПЕРЕРВА
 *        DI>=0 → (при DA=2) матч іде, DI = поточна хвилина
 *
 * ВАЖЛИВО: на відміну від match_h2h_export.js, тут НЕ перевіряється наявність
 * рахунку (homeScore/awayScore) для відрізнення "перерва" від "ще не почалось",
 * бо stage_monitor.js починає опитування вже через 10+ хвилин після старту
 * матчу — на цей момент матч точно вже стартував, і DA=2+DI=-1 однозначно
 * означає перерву.
 *
 * fsign кешується на FSIGN_TTL_MS, щоб не відкривати сторінку в Playwright
 * на кожну хвилинну перевірку — рефрешиться лише коли протух або фід
 * повернув щось незрозуміле.
 *
 * ─── Пам'ять / стабільність на слабкому VPS (доповнення) ───────────────────
 * Раніше браузер запускався один раз на весь час життя процесу (тижнями) з
 * прапорцем --single-process, який зливає browser+GPU+network+renderer в
 * ОДИН OS-процес. На малій пам'яті (1-2GB) це рано чи пізно призводило до
 * OOM/деградації, після чого ВСІ page.goto() починали висіти до таймауту
 * одночасно — незалежно від конкретного матчу (весь браузер — один процес,
 * і якщо він "захлинувся", валиться геть усе одразу).
 *
 * Тепер:
 *   - НЕ використовуємо --single-process / --no-zygote. Замість цього —
 *     --renderer-process-limit=1: один рендерер-процес на всі таби (значна
 *     економія пам'яті порівняно з дефолтним process-per-site), але
 *     browser/network-процеси лишаються окремими — без ефекту "все в одній
 *     кошику", що й ламало page.goto для всіх матчів одночасно.
 *   - --js-flags=--max-old-space-size=96 обрізає V8-хіп рендерера, щоб
 *     повільна витікаюча пам'ять не накопичувалась непомітно тижнями.
 *   - Жорстке блокування важких типів ресурсів (image/stylesheet/font/media)
 *     на рівні мережі (context.route) — нам потрібен лише заголовок x-fsign,
 *     сама верстка не рендериться візуально ніколи.
 *   - Періодичний recycle браузера (_maybeRecycle): за часом життя, за
 *     кількістю відкритих сторінок і за вільною пам'яттю системи
 *     (/proc/meminfo). Спрацьовує лениво — перед наступним _captureFsign.
 *   - Семафор на кількість одночасно відкритих сторінок (MAX_CONCURRENT_PAGES),
 *     щоб під час rescan()/catch-up вікон одразу для купи матчів не
 *     відкривалось 10+ сторінок паралельно на слабкому CPU/RAM.
 */

import { chromium } from 'playwright';
import https from 'https';
import zlib from 'zlib';
import fs from 'fs';

const FSIGN_TTL_MS              = Number(process.env.STAGE_FSIGN_TTL_MS) || 5 * 60_000; // 5 хв
const FSIGN_CAPTURE_TIMEOUT_MS  = Number(process.env.STAGE_FSIGN_CAPTURE_TIMEOUT_MS) || 20_000;
const FEED_PREFIX_DEFAULT       = '35';

// ─── Налаштування recycle / concurrency (тюнити під конкретний VPS) ────────
const BROWSER_RECYCLE_MS   = Number(process.env.STAGE_BROWSER_RECYCLE_MS)   || 3 * 60 * 60_000; // recycle раз на 3 год
const BROWSER_MAX_CAPTURES = Number(process.env.STAGE_BROWSER_MAX_CAPTURES) || 500;             // recycle після N відкритих сторінок
const MIN_FREE_MEM_MB      = Number(process.env.STAGE_MIN_FREE_MEM_MB)      || 150;             // recycle якщо вільної пам'яті менше
const MAX_CONCURRENT_PAGES = Number(process.env.STAGE_MAX_CONCURRENT_PAGES) || 3;               // макс. одночасних page.goto()

function getFreeMemMb() {
  try {
    const meminfo = fs.readFileSync('/proc/meminfo', 'utf8');
    const m = meminfo.match(/MemAvailable:\s+(\d+)\s+kB/);
    if (m) return Math.round(Number(m[1]) / 1024);
  } catch {}
  return null; // не Linux / немає доступу до /proc — просто пропускаємо цю перевірку
}

// ─── Простий семафор, щоб не відкривати забагато сторінок одночасно ────────
class Semaphore {
  constructor(max) {
    this._max = max;
    this._active = 0;
    this._queue = [];
  }
  async acquire() {
    if (this._active < this._max) {
      this._active++;
      return;
    }
    await new Promise(resolve => this._queue.push(resolve));
    this._active++;
  }
  release() {
    this._active--;
    const next = this._queue.shift();
    if (next) next();
  }
}

// ─── KV / feed helpers (той самий формат, що й у match_h2h_export.js) ───────

function parseKV(text) {
  const obj = {};
  if (!text) return obj;
  for (const pair of text.split('¬')) {
    const sep = pair.indexOf('÷');
    if (sep > 0) obj[pair.slice(0, sep)] = pair.slice(sep + 1);
  }
  return obj;
}

function fetchFeed(feedName, fsign, prefix = FEED_PREFIX_DEFAULT) {
  const url = `https://${prefix}.flashscore.ninja/${prefix}/x/feed/${feedName}`;
  const agent = new https.Agent({ keepAlive: false });
  return new Promise((resolve, reject) => {
    https.get(url, {
      agent,
      headers: {
        'x-fsign'        : fsign,
        'Referer'        : 'https://www.flashscore.ua/',
        'User-Agent'     : 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept'         : '*/*',
        'Accept-Encoding': 'gzip, deflate, br',
        'Origin'         : 'https://www.flashscore.ua',
      },
    }, res => {
      const enc = res.headers['content-encoding'];
      let stream = res;
      if (enc === 'gzip')         stream = res.pipe(zlib.createGunzip());
      else if (enc === 'deflate') stream = res.pipe(zlib.createInflate());
      else if (enc === 'br')      stream = res.pipe(zlib.createBrotliDecompress());
      const chunks = [];
      stream.on('data',  c => chunks.push(c));
      stream.on('end',   () => resolve(Buffer.concat(chunks).toString('utf-8')));
      stream.on('error', reject);
    }).on('error', reject);
  });
}

/**
 * status: 'not_started' | 'live' | 'break' | 'finished' | 'unknown'
 */
function parseDaDi(rawDc) {
  if (!rawDc || rawDc.length < 3) return { status: 'unknown', liveMinute: null };
  const kv = parseKV(rawDc.split('~')[0]);

  const da = kv['DA'];
  if (da === '3') return { status: 'finished',     liveMinute: null };
  if (da === '1') return { status: 'not_started',  liveMinute: null };

  const di = kv['DI'];
  if (di === undefined) return { status: 'unknown', liveMinute: null };

  const diNum = Number(di);
  if (diNum === -1) return { status: 'break', liveMinute: null };
  return { status: 'live', liveMinute: diNum };
}

// ─── Stage checker ───────────────────────────────────────────────────────────

class MatchStageChecker {
  constructor() {
    this._browser       = null;
    this._context       = null;
    this._launching     = null;
    this._fsignCache    = new Map(); // matchId -> { fsign, prefix, ts }
    this._launchedAt    = 0;
    this._captureCount  = 0;
    this._recycling     = null;      // проміс поточного recycle (щоб не паралелити)
    this._pageSemaphore = new Semaphore(MAX_CONCURRENT_PAGES);
  }

  async _launchBrowser() {
    const browser = await chromium.launch({
      headless: true,
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-gpu',
        '--disable-extensions',
        '--disable-background-networking',
        '--disable-background-timer-throttling',
        '--disable-backgrounding-occluded-windows',
        '--disable-renderer-backgrounding',
        '--disable-breakpad',
        '--disable-client-side-phishing-detection',
        '--disable-component-update',
        '--disable-default-apps',
        '--disable-sync',
        '--disable-translate',
        '--disable-features=Translate,site-per-process,IsolateOrigins',
        '--disable-hang-monitor',
        '--disable-ipc-flooding-protection',
        '--disable-popup-blocking',
        '--disable-prompt-on-repost',
        '--metrics-recording-only',
        '--no-first-run',
        '--no-default-browser-check',
        '--mute-audio',
        '--blink-settings=imagesEnabled=false',
        '--disable-application-cache',
        '--disable-cache',
        // КЛЮЧОВЕ замість --single-process: один рендерер-процес на всі
        // таби (економія пам'яті), але browser/network-процеси лишаються
        // окремими — тому "зависання" одного не кладе одразу весь браузер.
        '--renderer-process-limit=1',
        // Обрізаємо V8-хіп рендерера — повільний memory leak на довгій
        // дистанції впирається в ліміт і сам себе прибирає, а не росте
        // непомітно тижнями до OOM усього хоста.
        '--js-flags=--max-old-space-size=96',
      ],
    });

    const context = await browser.newContext();

    // Нам потрібен лише заголовок x-fsign із запиту до flashscore.ninja.
    // Картинки/шрифти/стилі/медіа для цього не потрібні взагалі — ріжемо їх
    // на рівні мережі (додатково до --blink-settings=imagesEnabled=false).
    await context.route('**/*', route => {
      const type = route.request().resourceType();
      if (type === 'image' || type === 'stylesheet' || type === 'font' || type === 'media') {
        return route.abort();
      }
      return route.continue();
    });

    this._launchedAt   = Date.now();
    this._captureCount = 0;

    return { browser, context };
  }

  async _ensureContext() {
    if (this._context) return this._context;
    if (this._launching) return this._launching;

    this._launching = (async () => {
      const { browser, context } = await this._launchBrowser();
      this._browser = browser;
      this._context = context;
      return context;
    })();

    const ctx = await this._launching;
    this._launching = null;
    return ctx;
  }

  /**
   * Ліниво перевіряє (перед кожним _captureFsign), чи не пора перезапустити
   * браузер: за віком, за кількістю відкритих сторінок або за нестачею
   * вільної пам'яті системи. Якщо recycle вже йде — просто чекаємо його.
   */
  async _maybeRecycle() {
    if (!this._browser) return;
    if (this._recycling) return this._recycling;

    const age    = Date.now() - this._launchedAt;
    const freeMb = getFreeMemMb();
    const reasons = [];
    if (age > BROWSER_RECYCLE_MS)                 reasons.push(`age ${(age / 60_000).toFixed(0)}min`);
    if (this._captureCount >= BROWSER_MAX_CAPTURES) reasons.push(`captures ${this._captureCount}`);
    if (freeMb !== null && freeMb < MIN_FREE_MEM_MB) reasons.push(`low mem ${freeMb}MB free`);

    if (reasons.length === 0) return;

    console.log(`[match-stage] ♻ Recycling browser (${reasons.join(', ')})`);

    this._recycling = (async () => {
      const oldBrowser = this._browser;
      const oldContext = this._context;
      this._browser = null;
      this._context = null;
      this._fsignCache.clear(); // fsign прив'язаний до старої сесії — валідність не гарантована
      try { await oldContext?.close(); } catch {}
      try { await oldBrowser?.close(); } catch {}
    })();

    await this._recycling;
    this._recycling = null;
  }

  /**
   * Відкриває легку сторінку матчу лише щоб перехопити x-fsign.
   * НЕ чекає на detailScore/DOM-статус, НЕ ходить за архівами/h2h —
   * це і робить перевірку набагато дешевшою за повний парсер.
   */
  async _captureFsign(matchId) {
    await this._maybeRecycle();
    await this._pageSemaphore.acquire();

    let page;
    try {
      const context = await this._ensureContext();
      this._captureCount++;
      page = await context.newPage();

      let fsign = '', prefix = FEED_PREFIX_DEFAULT;
      let resolveFsign;
      const fsignPromise = new Promise(res => { resolveFsign = res; });
      const handler = req => {
        const h = req.headers();
        if (h['x-fsign'] && req.url().includes('flashscore.ninja')) {
          const m = req.url().match(/https:\/\/(\d+)\.flashscore\.ninja/);
          resolveFsign({ fsign: h['x-fsign'], prefix: (m && m[1].length > 1) ? m[1] : FEED_PREFIX_DEFAULT });
        }
      };
      context.on('request', handler);

      try {
        await page.goto(`https://www.flashscore.ua/match/${matchId}/#/h2h/overall`, {
          waitUntil: 'domcontentloaded',
          timeout: 30_000,
        });

        const captured = await Promise.race([
          fsignPromise,
          new Promise((_, reject) => setTimeout(() => reject(new Error('fsign timeout')), FSIGN_CAPTURE_TIMEOUT_MS)),
        ]).catch(() => null);

        if (captured) { fsign = captured.fsign; prefix = captured.prefix; }
      } finally {
        context.off('request', handler);
      }

      return { fsign, prefix };
    } finally {
      await page?.close().catch(() => {});
      this._pageSemaphore.release();
    }
  }

  async _getFsign(matchId, { forceRefresh = false } = {}) {
    const cached = this._fsignCache.get(matchId);
    const isFresh = cached && (Date.now() - cached.ts) < FSIGN_TTL_MS;
    if (cached && isFresh && !forceRefresh) return cached;

    const captured = await this._captureFsign(matchId);
    if (!captured.fsign) {
      if (cached) return cached; // краще протухлий fsign, ніж жоден — спробуємо
      throw new Error(`Could not capture fsign for match ${matchId}`);
    }

    const entry = { ...captured, ts: Date.now() };
    this._fsignCache.set(matchId, entry);
    return entry;
  }

  /**
   * Повертає поточну стадію матчу.
   * @returns {Promise<{status: 'not_started'|'live'|'break'|'finished'|'unknown', liveMinute: number|null}>}
   */
  async checkStage(matchId, sportId = '5') {
    let { fsign, prefix } = await this._getFsign(matchId);
    let raw = await fetchFeed(`dc_${sportId}_${matchId}`, fsign, prefix).catch(() => '');
    let result = parseDaDi(raw);

    // Фід порожній/незрозумілий → fsign, ймовірно, протух. Пробуємо оновити один раз.
    if (result.status === 'unknown') {
      ({ fsign, prefix } = await this._getFsign(matchId, { forceRefresh: true }));
      raw = await fetchFeed(`dc_${sportId}_${matchId}`, fsign, prefix).catch(() => '');
      result = parseDaDi(raw);
    }

    return result;
  }

  async close() {
    try { await this._context?.close(); } catch {}
    try { await this._browser?.close(); } catch {}
    this._context = null;
    this._browser = null;
    this._fsignCache.clear();
  }
}

// Єдиний спільний інстанс на процес — щоб усі матчі перевірялись через
// один і той самий Chromium-браузер, а не плодили по процесу на матч.
export const matchStageChecker = new MatchStageChecker();
export { MatchStageChecker, parseDaDi };