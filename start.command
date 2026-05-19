#!/bin/bash
cd "$(dirname "$0")"

 
chmod +x ./node
xattr -cr . 2>/dev/null


echo "Введіть посилання на матч"
read MATCH_URL

if [ -z "$MATCH_URL" ]; then
    echo "Помилка. Посилання немає"
    sleep 3
    exit 1
fi

echo "Запуск парсинга..."
echo ""


./node src/match_h2h_export.js "matchUrl=$MATCH_URL"


echo "Натисніть Enter, щоб закрити."
read