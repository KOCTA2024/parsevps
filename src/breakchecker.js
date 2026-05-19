import { execSync } from "child_process";
import { fileURLToPath } from "url";
import path from "path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const matchId = process.argv[2];

if (!matchId) {
    console.error("match_id не передан");
    process.exit(1);
}

const checkerPath = path.join(__dirname, "breakchecker.js");
const parserPath = path.join(__dirname, "match_h2h_export.js");
const comment = `breakcheck_${matchId}`;

function addSelfToCron() {
    try {
        const existing = execSync(`crontab -l 2>/dev/null | grep "${comment}"`).toString();
        if (existing.trim()) return;
    } catch (_) {}

    const cronLine = `* * * * * node ${checkerPath} ${matchId} # ${comment}`;
    execSync(`(crontab -l 2>/dev/null; echo "${cronLine}") | crontab -`);
    console.log(`Добавлен в крон: ${comment}`);
}

function removeSelfFromCron() {
    execSync(`crontab -l 2>/dev/null | grep -v "${comment}" | crontab -`);
    console.log(`Удалён из крона: ${comment}`);
}

async function isHalfTime() {
    // TODO: твоя логика
    return false;
}

function runParser() {
    const matchUrl = `https://www.flashscore.com/match/${matchId}/`;
    execSync(`node ${parserPath} --matchUrl=${matchUrl}`, { stdio: "inherit" });
}

const halfTime = await isHalfTime();

addSelfToCron();

if (halfTime) {
    runParser();
    removeSelfFromCron();
    process.exit(0);
} else {
    console.log(`Матч ${matchId} — перерыва ещё нет`);
}