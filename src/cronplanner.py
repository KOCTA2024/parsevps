import os
import json
import subprocess
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(BASE_DIR, "matches.json"), "r") as f:
    data = json.load(f)

checker_path = os.path.join(BASE_DIR, "breakchecker.js")


def get_crontab():
    result = subprocess.run("crontab -l 2>/dev/null", shell=True, capture_output=True, text=True)
    return result.stdout


def write_crontab(content):
    subprocess.run("crontab -", input=content, shell=True, text=True)


def add_to_cron(command, comment, kickoff):
    current = get_crontab()

    if comment in current:
        print(f"Уже в кроне: {comment}")
        return

    minute = kickoff.minute
    hour = kickoff.hour
    day = kickoff.day
    month = kickoff.month

    new_line = f"{minute} {hour} {day} {month} * {command} # {comment}\n"
    write_crontab(current + new_line)
    print(f"Запланирован {comment} на {kickoff}")


for match in data["matches"]:
    if match["status"] != "scheduled":
        continue

    match_id = match["id"]
    kickoff = datetime.fromisoformat(match["kickoff"].replace("Z", "+00:00"))
    comment = f"kickoff_{match_id}"
    command = f"node {checker_path} {match_id}"

    add_to_cron(command, comment, kickoff)