@echo off
set /p MATCH_URL="Введіть посилання: "
node.exe src/match_h2h_export.js "matchUrl=%MATCH_URL%"
pause