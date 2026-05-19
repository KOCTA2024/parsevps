import { chromium } from 'playwright';
import https from 'https';;


async function getData() {
    const browser = await chromium.launch({ headless: true });
    const page = await browser.newPage();
    let fsign = '';
    const id = "O8PKW5lS";

    page.on('request', r => {
        const h = r.headers();
        if (h['x-fsign']) fsign = h['x-fsign'];
    });

    try {
        await page.goto(`https://www.flashscore.ua/match/${id}/#/h2h/overall`, { waitUntil: 'networkidle' });

        for (let i = 0; i < 20; i++) {
            if (fsign) break;
            await new Promise(r => setTimeout(r, 500));
        }

        if (!fsign) return;

        const url = `https://35.flashscore.ninja/35/x/feed/df_hh_5_${id}`;
        const options = {
            headers: {
                'x-fsign': fsign,
                'Referer': 'https://www.flashscore.ua/',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
        };

        https.get(url, options, (res) => {
            let rawData = '';
            res.on('data', chunk => rawData += chunk);
            res.on('end', () => {
                const blocks = rawData.split('~KB÷');
                
                const parseSection = (blockText) => {
                    if (!blockText) return [];
                    return blockText.split('~KC÷').slice(1).map(rawMatch => {
                        const getTag = (tag) => {
                            const match = rawMatch.match(new RegExp(`${tag}÷([^¬]+)`));
                            return match ? match[1] : null;
                        };

                        return {
                            timestamp: rawMatch.split('¬')[0],
                            matchId: getTag('KP'),
                            homeTeam: getTag('FH') || getTag('KJ'),
                            awayTeam: getTag('FK') || getTag('KK'),
                            scoreHome: getTag('KU'),
                            scoreAway: getTag('KT'),
                            result: getTag('KN')
                        };
                    });
                };

                console.log(JSON.stringify({
                    fsign,
                    team1: parseSection(blocks[1]).slice(0, 20),
                    team2: parseSection(blocks[2]).slice(0, 20),
                    h2h: parseSection(blocks[3]).slice(0, 5)
                }, null, 2));
            });
        });

    } catch (e) {
    } finally {
        await new Promise(r => setTimeout(r, 1000));
        await browser.close();
    }
}

getData();