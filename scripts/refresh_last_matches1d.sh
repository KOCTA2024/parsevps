#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-/home/ubuntu/parsevps}"
COMPOSE_SERVICE="${COMPOSE_SERVICE:-worker}"
SOURCE_DIR="/app/state/match_files"
DEST_DIR="${SOURCE_DIR}/last_matches1d"
ARCHIVE_PATH="${SOURCE_DIR}/last_matches1d.zip"
MODE="${1:---today}"
RETENTION_DAYS="${MATCH_FILES_RETENTION_DAYS:-3}"

case "$RETENTION_DAYS" in
  ''|*[!0-9]*)
    echo "MATCH_FILES_RETENTION_DAYS must be a non-negative integer" >&2
    exit 2
    ;;
esac

case "$MODE" in
  --today)
    START_EPOCH="$(TZ=Europe/Kyiv date -d 'today 00:00:00' +%s)"
    END_EPOCH="$(TZ=Europe/Kyiv date -d 'tomorrow 00:00:00' +%s)"
    ;;
  --yesterday-and-today)
    START_EPOCH="$(TZ=Europe/Kyiv date -d 'yesterday 00:00:00' +%s)"
    END_EPOCH="$(TZ=Europe/Kyiv date -d 'tomorrow 00:00:00' +%s)"
    ;;
  *)
    echo "Usage: $0 [--today|--yesterday-and-today]" >&2
    exit 2
    ;;
esac

cd "$PROJECT_DIR"

docker compose exec -T "$COMPOSE_SERVICE" sh -s -- \
  "$SOURCE_DIR" "$DEST_DIR" "$ARCHIVE_PATH" "$START_EPOCH" "$END_EPOCH" "$RETENTION_DAYS" <<'CONTAINER_SCRIPT'
set -eu

source_dir="$1"
dest_dir="$2"
archive_path="$3"
start_epoch="$4"
end_epoch="$5"
retention_days="$6"
staging_dir="${dest_dir}.tmp.$$"
old_dir="${dest_dir}.old.$$"
archive_tmp="${archive_path}.tmp.$$"
start_file="${source_dir}/.last_matches1d_start.$$"
end_file="${source_dir}/.last_matches1d_end.$$"

cleanup() {
  rm -rf -- "$staging_dir" "$old_dir"
  rm -f -- "$archive_tmp" "$start_file" "$end_file"
}
trap cleanup EXIT INT TERM

mkdir -p -- "$source_dir" "$staging_dir"
touch -d "@${start_epoch}" "$start_file"
touch -d "@${end_epoch}" "$end_file"

# Relative symlinks work in both the worker (/app/state) and nginx
# (/usr/share/nginx/html) containers, where the same volume has different roots.
find "$source_dir" -mindepth 1 -maxdepth 1 -type f \
  -name '*_result.json' ! -name '*_lines_result.json' \
  -newer "$start_file" ! -newer "$end_file" \
  -exec sh -c '
    staging_dir="$1"
    shift
    for source_file do
      base_name="${source_file##*/}"
      ln -s -- "../${base_name}" "${staging_dir}/${base_name}"
    done
  ' sh "$staging_dir" '{}' +

# Build a regular ZIP (with file contents, not symlink metadata) under a
# temporary name and publish it atomically with a stable URL.
python3 - "$staging_dir" "$archive_tmp" <<'PY'
import os
import sys
import zipfile

source_dir, archive_path = sys.argv[1:]
with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for entry in sorted(os.scandir(source_dir), key=lambda item: item.name):
        if entry.is_file(follow_symlinks=True):
            archive.write(entry.path, arcname=entry.name)
PY

if [ -d "$dest_dir" ]; then
  mv -- "$dest_dir" "$old_dir"
fi
mv -- "$staging_dir" "$dest_dir"
rm -rf -- "$old_dir"
mv -- "$archive_tmp" "$archive_path"

file_count="$(find "$dest_dir" -mindepth 1 -maxdepth 1 -type l | wc -l)"
echo "last_matches1d refreshed for Kyiv calendar date: ${file_count} symlinks; archive: ${archive_path}"

# Keep the public result archive bounded. With -mtime +3, GNU/BusyBox find
# retains the current day plus roughly the previous three full 24-hour periods.
# Only generated result files in the source root are eligible; state databases,
# logs, raw snapshots and the last_matches1d directory are left untouched.
find "$source_dir" -mindepth 1 -maxdepth 1 -type f \
  -name '*_result.json' -mtime "+${retention_days}" -delete
CONTAINER_SCRIPT
