/**
 * parse_lines.js  (betking.com.ua edition)
 * ─────────────────────────────────────────────────────────────────────────────
 * Scrapes bookmaker lines for a live basketball match from betking.com.ua.
 *
 * Key constraints:
 *  • Reuses the existing Playwright BrowserContext from match_h2h_export.js —
 *    NO second Chromium launch.
 *  • Opens / closes a single new page per call.
 *  • Must finish in < 25 s on a single-core 1 GB VPS.
 *  • Output format is compatible with the original line_result_<matchId>.json.
 *
 * FIX: Content is rendered inside Shadow DOM of <swiper-slide> Web Components.
 *      All scraping now uses page.evaluate() with recursive Shadow DOM traversal.
 *
 * FIX 2: Team name matching — Cyrillic → Latin transliteration + alias dictionary
 *         so teams like "Ферро" → "ferro", "Хімнасія" → "gimnasia" are found.
 *         Also dumps all lobby card names when match is not found (debug).
 *
 * Call site in match_h2h_export.js:
 *   await fetchAndSaveLines(
 *     matchId, DATA_DIR, participants, lineFilename,
 *     homeName, awayName,
 *     mainContext, liveStatus
 *   );
 */

import fs   from 'fs';
import path from 'path';

// ─── Constants ────────────────────────────────────────────────────────────────

const LOBBY_URL   = 'https://betking.com.ua/sports-book/?page=sport&sportId=67';
const NAV_TIMEOUT = 120_000;

// ─── Cyrillic → Latin transliteration table ───────────────────────────────────
// Ukrainian/Russian sports names transliterated to match betking's Latin display.

const TRANSLIT_MAP = {
  'а':'a','б':'b','в':'v','г':'g','ґ':'g','д':'d','е':'e','є':'ye','ж':'zh',
  'з':'z','и':'i','і':'i','ї':'yi','й':'y','к':'k','л':'l','м':'m','н':'n',
  'о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'kh','ц':'ts',
  'ч':'ch','ш':'sh','щ':'shch','ь':'','ю':'yu','я':'ya',
  // Russian extras
  'ё':'yo','э':'e','ъ':'','ы':'y',
};

/**
 * Convert a Cyrillic string to lowercase Latin.
 * "Ферро" → "ferro", "Хімнасія" → "khimnasiya"
 */
function transliterate(str) {
  return str.toLowerCase().split('').map(ch => TRANSLIT_MAP[ch] ?? ch).join('');
}

// ─── Known alias dictionary ───────────────────────────────────────────────────
// Key: Cyrillic display name (lowercase). Values: array of Latin substrings
// that betking may use. Add new teams as you encounter them.
//
// Rule: any value that is a substring of the betking card name (or vice-versa)
// is considered a match.

const TEAM_ALIASES = {
  // Argentina LNB
  'ферро':        ['ferro', 'ferro carril', 'ferrocarril'],
  'хімнасія':     ['gimnasia', 'gimnasia lp', 'gimn', 'himnasia'],
  'пеньяроль':    ['penarol', 'peñarol', 'penyarol'],
  'сан мартін':   ['san martin'],
  'сан лоренсо':  ['san lorenzo'],
  'атлетіко':     ['atletico', 'atlético'],
  'бока хуніорс': ['boca', 'boca juniors'],
  'рівер плейт':  ['river', 'river plate'],
  'реал мадрид':  ['real madrid'],
  'барселона':    ['barcelona', 'barça'],
  'фенербахче':   ['fenerbahce', 'fenerbahçe'],
  // Add more as needed …
};

/**
 * Build all search variants for a team name:
 *   1. The original string (lowercased) — covers Latin names passed directly
 *   2. Transliterated version
 *   3. Dictionary aliases (if any)
 * Returns array of unique lowercase strings.
 */
function teamVariants(name) {
  const orig   = name.toLowerCase().trim();
  const translit = transliterate(orig);
  const aliases  = TEAM_ALIASES[orig] ?? [];
  return [...new Set([orig, translit, ...aliases])];
}

/**
 * Returns true if any variant from setA overlaps with any variant from setB
 * using substring containment (either direction).
 */
function variantsMatch(variantsA, variantsB) {
  for (const a of variantsA)
    for (const b of variantsB)
      if (a.includes(b) || b.includes(a)) return true;
  return false;
}

// ─── Market-selection by game moment ─────────────────────────────────────────
// Always returns empty Set → scrape ALL available markets regardless of game period.
// The downstream Python analysis decides which lines it uses.

function marketsForStatus(_liveStatus = '') {
  return new Set();   // empty = scrape everything, always
}

// ─── Market name → category ───────────────────────────────────────────────────

function classifyMarket(name) {
  const n = name.toLowerCase().trim();

  // ── Win / 1x2 ──
  if (n.includes('переможець') || n === 'п1п2' || n === 'п1 п2' || n === 'перемога') return 'match_1x2';

  // ── Quarter 1x2 (e.g. "Третя чверть - 1x2") ──
  if ((n.includes('перша чверть') || n.includes('1-а чверть') || n.includes('1 чверть')) && n.includes('1x2')) return 'quarter_1x2_q1';
  if ((n.includes('друга чверть') || n.includes('2-а чверть') || n.includes('2 чверть')) && n.includes('1x2')) return 'quarter_1x2_q2';
  if ((n.includes('третя чверть') || n.includes('3-я чверть') || n.includes('3 чверть') || n.includes('третя')) && n.includes('1x2')) return 'quarter_1x2_q3';
  if ((n.includes('четверта чверть') || n.includes('4-а чверть') || n.includes('4 чверть') || n.includes('4-та чверть') || n.includes('четверта')) && n.includes('1x2')) return 'quarter_1x2_q4';

  // ── Quarter totals ──
  if (n.includes('тотал 1-ї чверті') || n.includes("тотал 1'ї чверті") || n.includes('тотал 1 чверті')) return 'quarter_total_q1';
  if (n.includes('тотал 2-ї чверті') || n.includes("тотал 2'ї чверті") || n.includes('тотал 2 чверті')) return 'quarter_total_q2';
  if (n.includes('тотал 3-ї чверті') || n.includes("тотал 3'ї чверті") || n.includes('тотал 3 чверті')) return 'quarter_total_q3';
  if (n.includes('тотал 4-ї чверті') || n.includes("тотал 4'ї чверті") || n.includes('тотал 4 чверті')) return 'quarter_total_q4';

  // ── Quarter draw-no-bet ──
  if ((n.includes('перша чверть') || n.includes('1-а чверть') || n.includes('1 чверть')) && n.includes('нічия без ставки')) return 'quarter_dnb_q1';
  if ((n.includes('друга чверть') || n.includes('2-а чверть') || n.includes('2 чверть')) && n.includes('нічия без ставки')) return 'quarter_dnb_q2';
  if ((n.includes('третя чверть') || n.includes('3-я чверть') || n.includes('3 чверть')) && n.includes('нічия без ставки')) return 'quarter_dnb_q3';
  if ((n.includes('четверта чверть') || n.includes('4-а чверть') || n.includes('4 чверть') || n.includes('4-та чверть')) && n.includes('нічия без ставки')) return 'quarter_dnb_q4';

  // ── Quarter "both teams score N" (e.g. "4 чверть: обидві команди наберуть N очок") ──
  if (n.includes('обидві команди наберуть')) {
    if (n.startsWith('1') || n.includes('перша чверть') || n.includes('1-а чверть')) return 'quarter_btts_q1';
    if (n.startsWith('2') || n.includes('друга чверть') || n.includes('2-а чверть'))  return 'quarter_btts_q2';
    if (n.startsWith('3') || n.includes('третя чверть') || n.includes('3-я чверть'))  return 'quarter_btts_q3';
    if (n.startsWith('4') || n.includes('четверта чверть') || n.includes('4'))         return 'quarter_btts_q4';
    return 'quarter_btts';
  }

  // ── Quarter race-to (e.g. "Четверта чверть - гонка до 15 очок") ──
  if (n.includes('гонка до')) {
    if (n.includes('перша чверть') || n.includes('1-а чверть') || n.includes('1 чверть')) return 'quarter_race_q1';
    if (n.includes('друга чверть') || n.includes('2-а чверть') || n.includes('2 чверть')) return 'quarter_race_q2';
    if (n.includes('третя чверть') || n.includes('3-я чверть') || n.includes('3 чверть')) return 'quarter_race_q3';
    if (n.includes('четверта чверть') || n.includes('4-а чверть') || n.includes('4 чверть') || n.includes('четверта')) return 'quarter_race_q4';
    return 'match_race';
  }

  // ── Win margin (e.g. "Перемога з різницею (вкл. овертайм)") ──
  if (n.includes('перемога з різницею')) return 'win_margin';

  // ── Half totals ──
  if (n.includes('тотал 1-ї половини') || n.includes('тотал першої половини')) return 'half_total_h1';
  if (n.includes('тотал 2-ї половини') || n.includes('тотал другої половини')) return 'half_total_h2';
  // note: bare "1 половина" / "2 половина" now handled under last_digit below first, catch below
  if (n.includes('1-а половина') && n.includes('тотал')) return 'half_total_h1';
  if (n.includes('2-а половина') && n.includes('тотал')) return 'half_total_h2';

  // ── Half last-digit / digit markets ──
  // (e.g. "2-а половина - Сума останніх цифр обох команд",
  //       "2-а половина - Остання цифра Ферро",
  //       "Ферро Остання цифра рахунку (вкл. овертайм)")
  if (n.includes('остання цифра') || n.includes('сума останніх цифр')) {
    if (n.includes('1-а половина') || n.includes('1 половина')) return 'half_last_digit_h1';
    if (n.includes('2-а половина') || n.includes('2 половина')) return 'half_last_digit_h2';
    return 'last_digit';
  }

  // ── Individual totals — must come BEFORE generic 'тотал' ──
  if (n.includes('індивідуальний тотал') || n.includes('тотал очків')) return 'ind_total';

  // ── Handicap ──
  if (n.includes('фора')) return 'match_handicap';

  // ── Quarter totals — format "Перша чверть - тотал" (quarter word BEFORE тотал) ──
  // Must come BEFORE the match_total catch-all.
  if ((n.includes('перша чверть') || n.includes('1-а чверть') || n.includes('1 чверть')) && n.includes('тотал')) return 'quarter_total_q1';
  if ((n.includes('друга чверть') || n.includes('2-а чверть') || n.includes('2 чверть')) && n.includes('тотал')) return 'quarter_total_q2';
  if ((n.includes('третя чверть') || n.includes('3-я чверть') || n.includes('3 чверть') || n.includes('третя')) && n.includes('тотал')) return 'quarter_total_q3';
  if ((n.includes('четверта чверть') || n.includes('4-а чверть') || n.includes('4 чверть') || n.includes('4-та чверть') || n.includes('четверта')) && n.includes('тотал')) return 'quarter_total_q4';

  // ── Match total (catch-all for anything with 'тотал' remaining) ──
  if (n.includes('тотал')) return 'match_total';

  return null;
}

// ─── Bet-label parser ─────────────────────────────────────────────────────────

function parseBetLabel(label, specialValue) {
  const l = label.trim();
  const sv = (specialValue ?? '').trim();

  // Over/under via "більше"/"менше"
  if (/більше/i.test(l)) {
    const m = (sv || l).match(/([\d.]+)/);
    return m ? { side: 'over',  line: parseFloat(m[1]) } : null;
  }
  if (/менше/i.test(l)) {
    const m = (sv || l).match(/([\d.]+)/);
    return m ? { side: 'under', line: parseFloat(m[1]) } : null;
  }

  // Handicap: specialValue holds "+6.5" / "-4.5", label holds team name
  if (sv && sv.match(/^[+-]?\d+\.?\d*$/)) {
    const v = parseFloat(sv);
    // We don't know home/away yet at this level — caller resolves by position
    return { side: 'raw_handicap', handicap: v };
  }

  // Fallback: plain handicap in label
  const hm = l.match(/^[Фф]?\s*([+-]?\d+\.?\d*)\s*$/);
  if (hm) {
    const v = parseFloat(hm[1]);
    return { side: v >= 0 ? 'home' : 'away', handicap: v };
  }

  if (/^п1$/i.test(l)) return { side: 'home' };
  if (/^п2$/i.test(l)) return { side: 'away' };

  return null;
}

// ─── Shadow DOM scraper (runs inside page.evaluate) ──────────────────────────

/**
 * Collects all market data from Shadow DOM roots on the page.
 * Returns array of { title, bets: [{label, specialValue, odd}] }
 * This function is serialised and sent to the browser — NO closures over outer scope.
 */
function collectMarketsFromShadowDOM() {
  // Recursively find all shadow roots, tracking visited roots to prevent duplicates.
  // Without the visited Set, nested shadow roots can be traversed multiple times
  // (once via their direct parent and again via an ancestor), causing market entries
  // to appear in the output more than once.
  function getAllShadowRoots(root, visited) {
    const roots = [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    let node = walker.nextNode();
    while (node) {
      if (node.shadowRoot && !visited.has(node.shadowRoot)) {
        visited.add(node.shadowRoot);
        roots.push(node.shadowRoot);
        roots.push(...getAllShadowRoots(node.shadowRoot, visited));
      }
      node = walker.nextNode();
    }
    return roots;
  }

  const visited = new Set([document]);
  const allRoots = [document, ...getAllShadowRoots(document, visited)];
  const markets = [];

  for (const root of allRoots) {
    const marketBoxes = root.querySelectorAll('[class*="EventDetailsMarketBoxContainer-sc-"]');
    for (const box of marketBoxes) {
      const titleEl = box.querySelector('[class*="EventDetailsMarketName-sc-"]');
      if (!titleEl) continue;
      const title = titleEl.textContent.trim();

      const bets = [];
      const buttons = box.querySelectorAll('button[class*="OddBoxButton-sc-"]');
      for (const btn of buttons) {
        // Label text (team name or більше/менше)
        const labelEl = btn.querySelector('[class*="OddLabel-sc-"]');
        // Special value (+6.5, -4.5, 175.5, etc.)
        const specialEl = btn.querySelector('[class*="OddSpecialValue-sc-"]');
        // Odd value
        const oddEl = btn.querySelector('[class*="OddValue-sc-"]');

        const label        = labelEl?.textContent.trim()  ?? '';
        const specialValue = specialEl?.textContent.trim() ?? '';
        const oddText      = oddEl?.textContent.trim().replace(',', '.') ?? '';
        const odd          = parseFloat(oddText) || null;

        if (label || specialValue) {
          bets.push({ label, specialValue, odd });
        }
      }

      if (bets.length > 0) markets.push({ title, bets });
    }
  }

  return markets;
}

// ─── Debug: dump all lobby cards (runs inside page.evaluate) ─────────────────

/**
 * Walks the entire Shadow DOM and collects all event card team names visible
 * in the lobby. Used when a match is not found to help diagnose naming mismatches.
 * Returns array of { home, away } objects.
 */
function collectAllLobbyCards() {
  function findInShadow(root) {
    const found = [];
    const cards = root.querySelectorAll('[class*="EventBoxContainer-sc-"]');
    for (const card of cards) {
      const names = Array.from(card.querySelectorAll('[class*="CompetitorName-sc-"]'))
        .map(e => e.textContent.trim());
      if (names.length >= 2)
        found.push({ home: names[0], away: names[1] });
      else if (names.length === 1)
        found.push({ home: names[0], away: '?' });
    }
    for (const el of root.querySelectorAll('*'))
      if (el.shadowRoot) found.push(...findInShadow(el.shadowRoot));
    return found;
  }
  return findInShadow(document);
}

// ─── Main scraper ─────────────────────────────────────────────────────────────

async function scrapeBetking(context, homeName, awayName, liveStatus = '') {
  const wantCategories = marketsForStatus(liveStatus);
  const scrapeAll      = wantCategories.size === 0;

  // Build multi-variant search arrays for robust matching
  const homeVariants = teamVariants(homeName);
  const awayVariants = teamVariants(awayName);

  console.log(`  [betking] homeVariants: ${JSON.stringify(homeVariants)}`);
  console.log(`  [betking] awayVariants: ${JSON.stringify(awayVariants)}`);

  // Keep simple norm strings for market-level matching (handicap labels etc.)
  const homeNorm = homeName.toLowerCase().trim();
  const awayNorm = awayName.toLowerCase().trim();

  const page = await context.newPage();
  page.setDefaultTimeout(NAV_TIMEOUT);

  let detailPage = page;

  try {
    // 1. Lobby
    console.log('  [betking] Navigating to lobby…');
    await page.goto(LOBBY_URL, { waitUntil: 'domcontentloaded', timeout: NAV_TIMEOUT });

    // Wait for event cards (may be inside shadow DOM too — use evaluate)
    await page.waitForFunction(() => {
      function hasShadowMatch(root) {
        if (root.querySelector('[class*="EventBoxContainer-sc-"]')) return true;
        for (const el of root.querySelectorAll('*')) {
          if (el.shadowRoot && hasShadowMatch(el.shadowRoot)) return true;
        }
        return false;
      }
      return hasShadowMatch(document);
    }, { timeout: NAV_TIMEOUT });

    // Wait for the card list to stabilise — on a slow single-core VPS the JS bundle
    // keeps rendering cards for several seconds after the first card appears.
    // Poll until the count stops growing for two consecutive checks, with a generous
    // per-poll delay to give the CPU time to finish rendering between checks.
    {
      const POLL_INTERVAL = 1200;  // ms between checks — generous for 1-core VPS
      const STABLE_ROUNDS = 2;     // how many equal counts in a row = stable
      const MAX_POLLS     = 10;    // bail out after 10 × 1200 ms = 12 s max
      let prevCount  = -1;
      let stableHits = 0;
      for (let i = 0; i < MAX_POLLS; i++) {
        await page.waitForTimeout(POLL_INTERVAL);
        const count = await page.evaluate(() => {
          function countCards(root) {
            let n = root.querySelectorAll('[class*="EventBoxContainer-sc-"]').length;
            for (const el of root.querySelectorAll('*'))
              if (el.shadowRoot) n += countCards(el.shadowRoot);
            return n;
          }
          return countCards(document);
        });
        console.log(`  [betking] Cards count poll ${i + 1}: ${count}`);
        if (count > 0 && count === prevCount) {
          stableHits++;
          if (stableHits >= STABLE_ROUNDS) {
            console.log(`  [betking] Card list stable at ${count} cards`);
            break;
          }
        } else {
          stableHits = 0;
        }
        prevCount = count;
      }
    }

    // 2. Find and click event card by team names.
    //    betking uses virtual/lazy scroll — cards below the viewport are not in the DOM.
    //    Strategy: scroll down in steps, attempt to find the card after each step.
    console.log('  [betking] Looking for match card (with scroll)…');

    // ─── Card search helpers (serialised into browser via page.evaluate) ────────

    // Core search: collects ALL unique cards from DOM+shadowDOM (deduped by text),
    // then tries to find one matching the given keyword sets.
    // Returns { clicked, foundNames, strategy } or null.
    //
    // matchMode:
    //   'both'  — card must match at least one homeKw AND one awayKw
    //   'home'  — card must match at least one homeKw (awayKws ignored)
    //   'away'  — card must match at least one awayKw (homeKws ignored)
    //
    // In 'home'/'away' mode we also require uniqueness — if more than one card
    // matches the single keyword we skip it (ambiguous).

    async function tryFindCard(homeKws, awayKws, matchMode, strategyLabel) {
      return page.evaluate(
        ([hKws, aKws, mode, label]) => {
          function wordIn(nameOnPage, kws) {
            const n = nameOnPage.toLowerCase().trim();
            return kws.some(k => n.includes(k) || k.includes(n));
          }

          // Collect all cards from DOM + shadow DOM, dedupe by joined competitor text
          function collectCards(root, seen, out) {
            const cards = root.querySelectorAll('[class*="EventBoxContainer-sc-"]');
            for (const card of cards) {
              const nameEls = Array.from(card.querySelectorAll('[class*="CompetitorName-sc-"]'));
              if (nameEls.length < 2) continue;
              const key = nameEls.map(e => e.textContent.trim().toLowerCase()).join('|');
              if (seen.has(key)) continue;
              seen.add(key);
              out.push({ card, names: nameEls.map(e => e.textContent.trim()) });
            }
            for (const el of root.querySelectorAll('*'))
              if (el.shadowRoot) collectCards(el.shadowRoot, seen, out);
          }

          const seen = new Set();
          const entries = [];
          collectCards(document, seen, entries);

          // Filter by match mode
          const matches = entries.filter(({ names }) => {
            const ns = names.map(n => n.toLowerCase());
            if (mode === 'both')
              return ns.some(n => wordIn(n, hKws)) && ns.some(n => wordIn(n, aKws));
            if (mode === 'home')
              return ns.some(n => wordIn(n, hKws));
            if (mode === 'away')
              return ns.some(n => wordIn(n, aKws));
            return false;
          });

          // For single-team modes require exactly one match (avoid ambiguity)
          if (mode !== 'both' && matches.length !== 1) return null;
          if (matches.length === 0) return null;

          matches[0].card.click();
          return { clicked: true, foundNames: matches[0].names, strategy: label };
        },
        [homeKws, awayKws, matchMode, strategyLabel]
      );
    }

    // ─── Scroll helpers ───────────────────────────────────────────────────────

    async function getScrollTop() {
      return page.evaluate(() => {
        function findScrollable(root) {
          for (const el of root.querySelectorAll('*')) {
            if (el.scrollHeight > el.clientHeight + 50 && el.clientHeight > 200) {
              const st = getComputedStyle(el);
              if (st.overflow === 'auto' || st.overflow === 'scroll' ||
                  st.overflowY === 'auto' || st.overflowY === 'scroll') return el;
            }
            if (el.shadowRoot) { const r = findScrollable(el.shadowRoot); if (r) return r; }
          }
          return null;
        }
        const el = findScrollable(document);
        return el ? el.scrollTop : window.scrollY;
      });
    }

    async function scrollBy(px) {
      await page.evaluate((amount) => {
        function findScrollable(root) {
          for (const el of root.querySelectorAll('*')) {
            if (el.scrollHeight > el.clientHeight + 50 && el.clientHeight > 200) {
              const st = getComputedStyle(el);
              if (st.overflow === 'auto' || st.overflow === 'scroll' ||
                  st.overflowY === 'auto' || st.overflowY === 'scroll') return el;
            }
            if (el.shadowRoot) { const r = findScrollable(el.shadowRoot); if (r) return r; }
          }
          return null;
        }
        const el = findScrollable(document);
        if (el) el.scrollTop += amount;
        else window.scrollBy(0, amount);
      }, px);
    }

    const SCROLL_STEP  = 600;
    const SCROLL_PAUSE = 1000;  // generous for 1-core VPS
    const MAX_SCROLLS  = 20;

    // ─── Build keyword lists for fallback word-by-word search ────────────────
    // All variants expanded + individual words longer than 3 chars, deduplicated.
    // Words are tried longest-first so more specific words win over short ones.

    function significantWords(variants) {
      const words = new Set();
      for (const v of variants) {
        // the full variant itself
        words.add(v);
        // individual words within the variant
        for (const w of v.split(/[\s\-.,]+/))
          if (w.length > 3) words.add(w);
      }
      // sort longest first — more specific = better
      return [...words].sort((a, b) => b.length - a.length);
    }

    const homeWords = significantWords(homeVariants);
    const awayWords = significantWords(awayVariants);

    console.log(`  [betking] homeWords: ${JSON.stringify(homeWords)}`);
    console.log(`  [betking] awayWords: ${JSON.stringify(awayWords)}`);

    // ─── Search pass — run after every scroll step ────────────────────────────
    // Returns clickResult or null.
    // Strategy order:
    //   1. Both teams full variants match (original behaviour)
    //   2. Each home word paired with each away word (word × word)
    //   3. Home word alone — unique card required
    //   4. Away word alone — unique card required

    async function searchPass() {
      // 1. Full match
      let r = await tryFindCard(homeVariants, awayVariants, 'both', 'full-variants');
      if (r) return r;

      // 2. Word × word
      for (const hw of homeWords) {
        for (const aw of awayWords) {
          r = await tryFindCard([hw], [aw], 'both', `word×word(${hw}×${aw})`);
          if (r) return r;
        }
      }

      // 3. Home word alone (unique)
      for (const hw of homeWords) {
        r = await tryFindCard([hw], [], 'home', `home-word(${hw})`);
        if (r) return r;
      }

      // 4. Away word alone (unique)
      for (const aw of awayWords) {
        r = await tryFindCard([], [aw], 'away', `away-word(${aw})`);
        if (r) return r;
      }

      return null;
    }

    // ─── Main find-and-click loop ─────────────────────────────────────────────

    console.log('  [betking] Looking for match card (with scroll)…');

    let clickResult = null;
    let scrollsDone = 0;

    clickResult = await searchPass();

    while (!clickResult && scrollsDone < MAX_SCROLLS) {
      const topBefore = await getScrollTop();
      await scrollBy(SCROLL_STEP);
      await page.waitForTimeout(SCROLL_PAUSE);
      scrollsDone++;

      clickResult = await searchPass();

      if (!clickResult) {
        const topAfter = await getScrollTop();
        if (topAfter <= topBefore) {
          console.log(`  [betking] Reached bottom of list after ${scrollsDone} scrolls`);
          break;
        }
      }
    }

    if (!clickResult) {
      // ── DEBUG: dump all visible cards so we know exactly what betking calls them ──
      console.warn(`  [betking] ⚠ Match not found: "${homeName}" vs "${awayName}" (scrolled ${scrollsDone} times)`);
      console.warn(`  [betking] 🔍 Dumping all lobby cards for diagnosis:`);

      const allCards = await page.evaluate(collectAllLobbyCards);
      if (allCards.length === 0) {
        console.warn('  [betking]   (no cards found — page may not have loaded correctly)');
      } else {
        allCards.forEach((c, i) => {
          console.warn(`  [betking]   card[${i}]: "${c.home}" vs "${c.away}"`);
        });
      }
      console.warn(`  [betking] 💡 Check if the match is listed above. homeVariants=${JSON.stringify(homeVariants)} awayVariants=${JSON.stringify(awayVariants)}`);
      return null;
    }

    console.log(`  [betking] ✅ Match card clicked after ${scrollsDone} scrolls — found: ${JSON.stringify(clickResult.foundNames)} (strategy: ${clickResult.strategy})`);

    // 3. Wait for new tab or navigation to match detail
    console.log('  [betking] Waiting for detail page…');
    const newTab = await context.waitForEvent('page', { timeout: 5000 }).catch(() => null);
    detailPage = newTab ?? page;

    // Wait until market boxes appear in shadow DOM
    await detailPage.waitForFunction(() => {
      function hasMarkets(root) {
        if (root.querySelector('[class*="EventDetailsMarketBoxContainer-sc-"]')) return true;
        for (const el of root.querySelectorAll('*')) {
          if (el.shadowRoot && hasMarkets(el.shadowRoot)) return true;
        }
        return false;
      }
      return hasMarkets(document);
    }, { timeout: NAV_TIMEOUT });

    console.log('  [betking] Match detail loaded — scraping markets…');

    // 4. Wait for tabs to render (Shadow DOM tabs may arrive later than market boxes,
    //    especially on slow single-core VPS). Poll up to 5 s with 500 ms steps.
    {
      const TAB_POLL_INTERVAL = 500;
      const TAB_POLL_MAX      = 10;   // 10 × 500 ms = 5 s max
      for (let i = 0; i < TAB_POLL_MAX; i++) {
        const tabCount = await detailPage.evaluate(() => {
          function countTabs(root) {
            let n = root.querySelectorAll('[class*="EventDetailsTabContainer-sc-"]').length;
            for (const el of root.querySelectorAll('*'))
              if (el.shadowRoot) n += countTabs(el.shadowRoot);
            return n;
          }
          return countTabs(document);
        });
        console.log(`  [betking] Tab poll ${i + 1}: ${tabCount} tab(s)`);
        if (tabCount > 0) break;
        await detailPage.waitForTimeout(TAB_POLL_INTERVAL);
      }
    }

    // Discover available tabs in shadow DOM
    const tabs = await detailPage.evaluate(() => {
      function findInShadow(root) {
        const found = [];
        root.querySelectorAll('[class*="EventDetailsTabContainer-sc-"]')
          .forEach((el, i) => found.push({ text: el.textContent.trim(), idx: found.length }));
        for (const el of root.querySelectorAll('*'))
          if (el.shadowRoot) {
            const sub = findInShadow(el.shadowRoot);
            sub.forEach(s => { s.idx = found.length; found.push(s); });
          }
        return found;
      }
      return findInShadow(document);
    });
    console.log(`  [betking] Tabs found: ${tabs.map(t => t.text).join(', ') || '(none)'}`);

    // Click a tab by its global index and wait for React re-render.
    // On slow VPS use a longer settle delay (1 s instead of 700 ms).
    async function clickTab(idx) {
      await detailPage.evaluate((tabIdx) => {
        let count = 0;
        function clickInShadow(root) {
          for (const el of root.querySelectorAll('[class*="EventDetailsTabContainer-sc-"]')) {
            if (count++ === tabIdx) { el.click(); return true; }
          }
          for (const el of root.querySelectorAll('*'))
            if (el.shadowRoot && clickInShadow(el.shadowRoot)) return true;
          return false;
        }
        clickInShadow(document);
      }, idx);
      await detailPage.waitForTimeout(1000);   // generous settle for 1-core VPS
    }

    // Collect markets from all tabs, dedup by title.
    // IMPORTANT: specific period tabs (1-а половина, 2-га чверть, …) carry the
    // freshest odds. Generic tabs (Усі / Головні) may show stale/suspended lines
    // for markets that have already been updated in a specific tab.
    // Scrape specific tabs FIRST so their data wins the dedup, then fill in
    // anything remaining from the generic tabs.
    const seenTitles = new Set();
    const rawMarkets = [];

    function mergeMarkets(fresh) {
      for (const m of fresh)
        if (!seenTitles.has(m.title)) { seenTitles.add(m.title); rawMarkets.push(m); }
    }

    const TAB_SKIP    = ['конструктор'];
    const TAB_GENERIC = ['усі', 'головні', 'всі'];

    const genericTabs  = [];
    const specificTabs = [];
    for (const tab of tabs) {
      const tl = tab.text.toLowerCase();
      if (TAB_SKIP.some(s => tl.includes(s))) continue;
      if (TAB_GENERIC.some(s => tl.includes(s))) genericTabs.push(tab);
      else specificTabs.push(tab);
    }

    console.log(`  [betking] Specific tabs: ${specificTabs.map(t => t.text).join(', ') || '(none)'}`);
    console.log(`  [betking] Generic  tabs: ${genericTabs.map(t => t.text).join(', ')  || '(none)'}`);

    // 1. Specific tabs first (freshest data)
    for (const tab of specificTabs) {
      await clickTab(tab.idx);
      mergeMarkets(await detailPage.evaluate(collectMarketsFromShadowDOM));
    }

    // 2. Generic tabs — fill in anything not yet seen
    for (const tab of genericTabs) {
      await clickTab(tab.idx);
      mergeMarkets(await detailPage.evaluate(collectMarketsFromShadowDOM));
    }

    // 3. Fallback: if no tabs at all (or nothing collected), scrape whatever is
    //    currently rendered on the page. This keeps the VPS case working even when
    //    the tab bar never appears.
    if (rawMarkets.length === 0) {
      console.log('  [betking] No tabs found or empty result — scraping active view directly');
      mergeMarkets(await detailPage.evaluate(collectMarketsFromShadowDOM));
    }

    // Skip locked/suspended buttons: filter bets with null odds out of each market
    // so positional parsers (match_1x2, quarter_dnb, quarter_race) get clean arrays.
    for (const m of rawMarkets) {
      m.bets = m.bets.filter(b => b.odd !== null);
    }

    console.log(`  [betking] Total unique markets after all tabs: ${rawMarkets.length}`);

    // 5. Transform into output structure
    const result = {
      match_1x2      : [],
      match_handicap : [],
      match_total    : [],
      half_total     : [],
      quarter_total  : [],
      quarter_dnb    : [],
      quarter_1x2    : [],   // quarter winner markets (Q1-Q4)
      quarter_btts   : [],   // both teams score N in quarter
      quarter_race   : [],   // race-to-N in quarter
      win_margin     : [],   // win margin buckets
      last_digit     : [],   // last digit / sum of digits markets
      home_ind_total : [],
      away_ind_total : [],
    };

    for (const { title, bets } of rawMarkets) {
      const cat = classifyMarket(title);
      if (!cat) {
        console.log(`  [betking] Unclassified market: "${title}"`);
        continue;
      }

      const baseCategory = cat.replace(/_q[1-4]$/, '').replace(/_h[12]$/, '');
      if (!scrapeAll && !wantCategories.has(baseCategory)) continue;

      // ── match_1x2 ──
      if (cat === 'match_1x2') {
        // Keep only one entry — prefer "Переможець", skip duplicates
        if (result.match_1x2.length === 0 && bets.length >= 2) {
          result.match_1x2.push({
            _description: 'Переможець ВСЬОГО МАТЧУ (не чверті). homeOdd = коефіцієнт на перемогу хазяїв, awayOdd = на перемогу гостей. Нічия не передбачена (баскетбол).',
            scope   : 'Match',
            homeOdd : bets[0]?.odd ?? null,
            awayOdd : bets[1]?.odd ?? null,
          });
        }

      // ── match_handicap ──
      } else if (cat === 'match_handicap') {
        // Buttons come in pairs: [home+X, away-X] or multiple lines
        // specialValue holds the handicap number, label holds team name
        const pairs = {};
        for (const bet of bets) {
          if (!bet.specialValue) continue;
          const hv = parseFloat(bet.specialValue);
          if (isNaN(hv)) continue;
          const key = Math.abs(hv).toFixed(1);
          if (!pairs[key]) pairs[key] = { home: null, away: null };

          const teamName     = bet.label.toLowerCase().trim();
          const teamVariantsBk = [teamName];

          // Match handicap labels against both Cyrillic and Latin variants
          const isHome = variantsMatch(homeVariants, teamVariantsBk) ||
                         teamName.includes(homeNorm) || homeNorm.includes(teamName);
          const isAway = variantsMatch(awayVariants, teamVariantsBk) ||
                         teamName.includes(awayNorm) || awayNorm.includes(teamName);

          // home gets specialValue as-is (e.g. +6.5), away gets opposite sign
          if (isHome) pairs[key].home = { handicap: hv, odd: bet.odd };
          else if (isAway) pairs[key].away = { handicap: -hv, odd: bet.odd };
        }

        for (const { home, away } of Object.values(pairs)) {
          if (!home && !away) continue;
          result.match_handicap.push({
            _description: `Азіатський гандикап на ВЕСЬ МАТЧ. handicap — фора хазяїв (від'ємне = хазяї фаворити, наприклад -3.5 означає "хазяї мінус 3.5 очка"). homeHcpOdd — коефіцієнт якщо ставити на хазяїв з цією форою, awayHcpOdd — на гостей з протилежною форою (+3.5). НЕ стосується жодної окремої чверті.`,
            scope      : 'Match',
            handicap   : home?.handicap ?? (away ? away.handicap : null),
            homeHcpOdd : home?.odd ?? null,
            awayHcpOdd : away?.odd ?? null,
          });
        }

      // ── match_total ──
      } else if (cat === 'match_total') {
        for (const bet of bets) {
          // specialValue holds the line number, label holds більше/менше
          const lineVal = parseFloat(bet.specialValue) || null;
          const p = parseBetLabel(bet.label, bet.specialValue);
          if (!p || p.line == null) continue;
          const line = p.line ?? lineVal;
          if (!line) continue;
          let e = result.match_total.find(x => x.line === line);
          if (!e) { e = { _description: `Тотал ВСЬОГО МАТЧУ (сума очок обох команд за 4 чверті). line — межа тоталу (наприклад 133.5). overOdd — ставка "більше ${line}", underOdd — ставка "менше ${line}". НЕ плутати з quarter_total — це весь матч.`, scope: 'Match', line, overOdd: null, underOdd: null }; result.match_total.push(e); }
          if (p.side === 'over')  e.overOdd  = bet.odd;
          if (p.side === 'under') e.underOdd = bet.odd;
        }

      // ── half totals ──
      } else if (cat === 'half_total_h1' || cat === 'half_total_h2') {
        const scope = cat === 'half_total_h1' ? 'H1' : 'H2';
        for (const bet of bets) {
          const p = parseBetLabel(bet.label, bet.specialValue);
          if (!p || p.line == null) continue;
          let e = result.half_total.find(x => x.scope === scope && x.line === p.line);
          if (!e) { e = { _description: `Тотал за ПОЛОВИНУ матчу (2 чверті разом). scope="${scope}" — ${scope === 'H1' ? 'перша половина (Q1+Q2)' : 'друга половина (Q3+Q4)'}. line — межа, overOdd/underOdd — коефіцієнти більше/менше.`, scope, line: p.line, overOdd: null, underOdd: null }; result.half_total.push(e); }
          if (p.side === 'over')  e.overOdd  = bet.odd;
          if (p.side === 'under') e.underOdd = bet.odd;
        }

      // ── quarter totals ──
      } else if (cat.startsWith('quarter_total_')) {
        const scopeMap = { quarter_total_q1:'Q1', quarter_total_q2:'Q2', quarter_total_q3:'Q3', quarter_total_q4:'Q4' };
        const scope = scopeMap[cat];
        for (const bet of bets) {
          const p = parseBetLabel(bet.label, bet.specialValue);
          if (!p || p.line == null) continue;
          let e = result.quarter_total.find(x => x.scope === scope && x.line === p.line);
          const quarterLabel = { Q1: 'перша чверть', Q2: 'друга чверть', Q3: 'третя чверть', Q4: 'четверта чверть' };
          if (!e) { e = { _description: `Тотал ТІЛЬКИ ЗА ${scope} (${quarterLabel[scope]}). Це НЕ тотал матчу — лише очки набрані в одній чверті обома командами. line — межа, overOdd/underOdd — коефіцієнти більше/менше.`, scope, line: p.line, overOdd: null, underOdd: null }; result.quarter_total.push(e); }
          if (p.side === 'over')  e.overOdd  = bet.odd;
          if (p.side === 'under') e.underOdd = bet.odd;
        }

      // ── quarter draw-no-bet ──
      } else if (cat.startsWith('quarter_dnb_')) {
        const scopeMap = { quarter_dnb_q1:'Q1', quarter_dnb_q2:'Q2', quarter_dnb_q3:'Q3', quarter_dnb_q4:'Q4' };
        const scope = scopeMap[cat];
        if (bets.length >= 2) {
          // Buttons order: home team first, away team second (same pattern as match_1x2)
          const homeOdd = bets[0]?.odd ?? null;
          const awayOdd = bets[1]?.odd ?? null;
          if (!result.quarter_dnb.find(x => x.scope === scope)) {
            const quarterLabel = { Q1: 'перша чверть', Q2: 'друга чверть', Q3: 'третя чверть', Q4: 'четверта чверть' };
            result.quarter_dnb.push({
              _description: `"Нічия без ставки" за ${scope} (${quarterLabel[scope]}). Якщо чверть завершиться нічиєю — ставка повертається. homeOdd — коефіцієнт на перемогу хазяїв у цій чверті, awayOdd — на перемогу гостей. НЕ стосується результату матчу в цілому.`,
              scope, homeOdd, awayOdd,
            });
          }
        }

      // ── individual totals ──
      } else if (cat === 'ind_total') {
        const titleLower = title.toLowerCase();
        // Try Cyrillic match first, then Latin/alias variants
        const isHome = titleLower.includes(homeNorm) ||
                       homeVariants.some(v => titleLower.includes(v)) ||
                       (homeNorm.split(' ').some(w => w.length > 3 && titleLower.includes(w)));
        const isAway = titleLower.includes(awayNorm) ||
                       awayVariants.some(v => titleLower.includes(v)) ||
                       (awayNorm.split(' ').some(w => w.length > 3 && titleLower.includes(w)));
        const target = isHome ? result.home_ind_total : isAway ? result.away_ind_total : null;
        if (!target) {
          console.log(`  [betking] ind_total: cannot match "${title}" to home="${homeName}" or away="${awayName}"`);
          continue;
        }
        for (const bet of bets) {
          const p = parseBetLabel(bet.label, bet.specialValue);
          if (!p || p.line == null) continue;
          let e = target.find(x => x.line === p.line);
          const teamLabel = isHome ? `хазяїв (${homeName})` : `гостей (${awayName})`;
          if (!e) { e = { _description: `Індивідуальний тотал ТІЛЬКИ ${teamLabel} за матч. line — межа очок цієї команди. overOdd — команда набере більше ${p.line}, underOdd — менше ${p.line}. Не плутати з match_total (там обидві команди разом).`, scope: 'Match', line: p.line, overOdd: null, underOdd: null }; target.push(e); }
          if (p.side === 'over')  e.overOdd  = bet.odd;
          if (p.side === 'under') e.underOdd = bet.odd;
        }

      // ── quarter 1x2 (quarter winner) ──
      } else if (cat.startsWith('quarter_1x2_')) {
        const scopeMap = { quarter_1x2_q1:'Q1', quarter_1x2_q2:'Q2', quarter_1x2_q3:'Q3', quarter_1x2_q4:'Q4' };
        const scope = scopeMap[cat];
        const quarterLabel1x2 = { Q1: 'перша чверть', Q2: 'друга чверть', Q3: 'третя чверть', Q4: 'четверта чверть' };
        if (bets.length >= 2 && !result.quarter_1x2.find(x => x.scope === scope)) {
          result.quarter_1x2.push({
            _description: `Переможець ТІЛЬКИ ${scope} (${quarterLabel1x2[scope]}) — не матчу. homeOdd — хазяї виграють цю чверть, drawOdd — нічия в чверті (рідко, але можлива), awayOdd — гості виграють цю чверть.`,
            scope,
            title,
            homeOdd : bets[0]?.odd ?? null,
            drawOdd : bets.length >= 3 ? (bets[1]?.odd ?? null) : null,
            awayOdd : bets.length >= 3 ? (bets[2]?.odd ?? null) : (bets[1]?.odd ?? null),
          });
        }

      // ── quarter both-teams-score-N ──
      } else if (cat.startsWith('quarter_btts')) {
        const scopeMap = { quarter_btts_q1:'Q1', quarter_btts_q2:'Q2', quarter_btts_q3:'Q3', quarter_btts_q4:'Q4', quarter_btts:'Match' };
        const scope = scopeMap[cat] ?? 'Match';
        const thresholdM = title.match(/наберуть\s+(\d+)/i);
        const threshold = thresholdM ? parseInt(thresholdM[1]) : null;
        const yesOdd = bets[0]?.odd ?? null;
        const noOdd  = bets[1]?.odd ?? null;
        const quarterLabelBtts = { Q1: 'першій чверті', Q2: 'другій чверті', Q3: 'третій чверті', Q4: 'четвертій чверті', Match: 'матчі' };
        result.quarter_btts.push({
          _description: `Чи наберуть ОБИДВІ команди щонайменше ${threshold ?? '?'} очок у ${quarterLabelBtts[scope] ?? scope}. yesOdd — так (обидві наберуть), noOdd — ні (хоча б одна не набере). Це ставка на результативність обох команд, не на переможця.`,
          scope, title, threshold, yesOdd, noOdd,
        });

      // ── quarter race-to-N ──
      } else if (cat.startsWith('quarter_race') || cat === 'match_race') {
        const scopeMap = { quarter_race_q1:'Q1', quarter_race_q2:'Q2', quarter_race_q3:'Q3', quarter_race_q4:'Q4', match_race:'Match' };
        const scope = scopeMap[cat] ?? 'Match';
        const targetM = title.match(/до\s+(\d+)/i);
        const target  = targetM ? parseInt(targetM[1]) : null;
        const quarterLabelRace = { Q1: 'першій чверті', Q2: 'другій чверті', Q3: 'третій чверті', Q4: 'четвертій чверті', Match: 'матчі' };
        if (bets.length >= 2) {
          result.quarter_race.push({
            _description: `"Гонка до ${target ?? '?'} очок" у ${quarterLabelRace[scope] ?? scope} — яка команда ПЕРШОЮ набере ${target ?? '?'} очок саме в цьому ігровому відрізку. homeOdd — хазяї першими наберуть ${target ?? '?'}, awayOdd — гості. Це НЕ тотал і НЕ переможець чверті.`,
            scope, title, target,
            homeOdd : bets[0]?.odd ?? null,
            awayOdd : bets[1]?.odd ?? null,
          });
        }

      // ── win margin ──
      } else if (cat === 'win_margin') {
        for (const bet of bets) {
          if (bet.odd) result.win_margin.push({
            _description: `Перемога переможця матчу з різницею очок у діапазоні "${bet.label ?? bet.specialValue}". Наприклад "1-5" — переможець виграє на 1-5 очок. odd — коефіцієнт на цей діапазон.`,
            label: bet.label, specialValue: bet.specialValue, odd: bet.odd,
          });
        }

      // ── last digit / digit sum markets ──
      } else if (cat === 'last_digit' || cat.startsWith('half_last_digit')) {
        const scope = cat === 'half_last_digit_h1' ? 'H1' : cat === 'half_last_digit_h2' ? 'H2' : 'Match';
        const scopeText = scope === 'H1' ? 'першої половини' : scope === 'H2' ? 'другої половини' : 'матчу';
        for (const bet of bets) {
          if (bet.odd) result.last_digit.push({
            _description: `Ставка на останню цифру рахунку або суму останніх цифр обох команд ${scopeText}. label — конкретна цифра або діапазон на яку ставка. Екзотичний ринок, не стосується тоталів чи переможця.`,
            scope, title, label: bet.label, specialValue: bet.specialValue, odd: bet.odd,
          });
        }
      }
    }

    // Sort totals by line value
    for (const key of ['match_total', 'half_total', 'quarter_total', 'quarter_dnb', 'home_ind_total', 'away_ind_total'])
      result[key].sort((a, b) => (a.line ?? 0) - (b.line ?? 0));

    // Drop entries where ALL odds are null (both sides missing = no market)
    for (const key of ['match_total', 'half_total', 'quarter_total', 'home_ind_total', 'away_ind_total'])
      result[key] = result[key].filter(x => x.overOdd !== null || x.underOdd !== null);

    for (const key of ['match_handicap'])
      result[key] = result[key].filter(x => x.homeHcpOdd !== null || x.awayHcpOdd !== null);

    for (const key of ['match_1x2', 'quarter_1x2', 'quarter_dnb'])
      result[key] = result[key].filter(x => x.homeOdd !== null || x.awayOdd !== null);

    // Remove empty arrays
    for (const key of Object.keys(result))
      if (Array.isArray(result[key]) && result[key].length === 0) delete result[key];

    // ─── Schema description for downstream model ─────────────────────────────
    // Explains every key so the model can never confuse scope or market type.
    result._schema = {
      _readme: 'Кожен масив описує окремий тип ставки. scope вказує на який ігровий відрізок поширюється ставка: "Match"=весь матч, "H1"=перша половина(Q1+Q2), "H2"=друга половина(Q3+Q4), "Q1"/"Q2"/"Q3"/"Q4"=конкретна чверть. НІКОЛИ не використовуй дані з quarter_* для аналізу матчу в цілому і навпаки. Всі поля *Odd (homeOdd, awayOdd, overOdd, underOdd, homeHcpOdd, awayHcpOdd, yesOdd, noOdd) — це десяткові коефіцієнти букмекера: число на яке множиться сума ставки у разі виграшу (наприклад 1.83 означає прибуток 83% від суми ставки).',
      match_1x2:      'Переможець ВСЬОГО матчу. scope завжди "Match". homeOdd/awayOdd.',
      match_handicap: 'Азіатський гандикап на ВЕСЬ матч. scope="Match". handicap<0 = фора хазяїв (вони фаворити). homeHcpOdd/awayHcpOdd.',
      match_total:    'Тотал ВСЬОГО матчу (сума очок обох команд за всі чверті). scope="Match". line=межа, overOdd/underOdd.',
      half_total:     'Тотал за ПОЛОВИНУ матчу (2 чверті). scope="H1" або "H2". line=межа, overOdd/underOdd.',
      quarter_total:  'Тотал ТІЛЬКИ за одну чверть. scope="Q1"/"Q2"/"Q3"/"Q4". НЕ ПЛУТАТИ з match_total — це набагато менше очок (30-40, не 130+). line=межа, overOdd/underOdd.',
      quarter_dnb:    '"Нічия без ставки" за одну чверть. scope=Q1-Q4. При нічиї в чверті — ставка повертається. homeOdd/awayOdd.',
      quarter_1x2:    'Переможець ОДНІЄЇ чверті. scope=Q1-Q4. homeOdd/drawOdd/awayOdd. drawOdd може бути null.',
      quarter_btts:   'Чи наберуть ОБИДВІ команди мінімум threshold очок у чверті. scope=Q1-Q4. yesOdd/noOdd.',
      quarter_race:   '"Гонка до N очок" — хто ПЕРШИМ набере target очок у конкретному ігровому відрізку. scope=Q1-Q4 або Match. target=кількість очок. homeOdd/awayOdd. Це НЕ тотал і НЕ переможець.',
      home_ind_total: 'Індивідуальний тотал ТІЛЬКИ хазяїв за матч. scope="Match". line/overOdd/underOdd.',
      away_ind_total: 'Індивідуальний тотал ТІЛЬКИ гостей за матч. scope="Match". line/overOdd/underOdd.',
      win_margin:     'Різниця очок переможця матчу (bucket-ставка). label=діапазон типу "1-5" або "6-10". odd=коефіцієнт.',
      last_digit:     'Остання цифра рахунку або сума останніх цифр обох команд. scope=Match/H1/H2. Екзотичний ринок.',
    };

    return result;

  } finally {
    if (detailPage !== page) await detailPage.close().catch(() => {});
    await page.close().catch(() => {});
  }
}

// ─── Public API ───────────────────────────────────────────────────────────────

/**
 * @param {string}  matchId
 * @param {string}  outputDir
 * @param {object}  participants   — { homeId, awayId } (compat only, unused)
 * @param {string|null} lineFilename
 * @param {string}  homeName       — home team display name (plain string)
 * @param {string}  awayName       — away team display name (plain string)
 * @param {import('playwright').BrowserContext} context
 * @param {string}  [liveStatus='']
 */
export async function fetchAndSaveLines(
  matchId,
  outputDir,
  participants = null,
  lineFilename  = null,
  homeName,
  awayName,
  context,
  liveStatus    = ''
) {
  lineFilename = lineFilename ?? `line_result_${matchId}.json`;

  if (!homeName || !awayName) {
    const msg = `fetchAndSaveLines: homeName/awayName not provided (got "${homeName}" / "${awayName}") — skipping betking scrape`;
    console.warn(`  [betking] ⚠ ${msg}`);
    fs.mkdirSync(outputDir, { recursive: true });
    const err = { error: 'missing_team_names', matchId, source: 'betking' };
    fs.writeFileSync(path.join(outputDir, lineFilename), JSON.stringify(err, null, 2), 'utf-8');
    return err;
  }

  console.log(`\n--- Завантаження ліній (betking.com.ua)… ---`);
  console.log(`  Match: "${homeName}" vs "${awayName}"`);
  console.log(`  liveStatus: "${liveStatus}"`);

  let parsed = null;
  try {
    parsed = await scrapeBetking(context, homeName, awayName, liveStatus);
  } catch (e) {
    console.warn(`  [betking] ⚠ Помилка скрапінгу: ${e.message}`);
    console.warn(e.stack);
  }

  fs.mkdirSync(outputDir, { recursive: true });
  const outPath = path.join(outputDir, lineFilename);

  if (!parsed) {
    const empty = { error: 'scrape_failed', matchId, source: 'betking', homeName, awayName };
    fs.writeFileSync(outPath, JSON.stringify(empty, null, 2), 'utf-8');
    return empty;
  }

  parsed.meta = { source: 'betking', fetchedAt: new Date().toISOString(), matchId };
  fs.writeFileSync(outPath, JSON.stringify(parsed, null, 2), 'utf-8');
  console.log(`✅ Лінії збережено: ${outPath}`);

  const KEYS = ['match_1x2','match_handicap','match_total','half_total','quarter_total','quarter_dnb','quarter_1x2','quarter_btts','quarter_race','win_margin','last_digit','home_ind_total','away_ind_total'];
  for (const k of KEYS) if (parsed[k]) console.log(`  ${k}: ${parsed[k].length} рядків`);

  return parsed;
}