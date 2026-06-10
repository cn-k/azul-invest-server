#!/bin/bash
# Scraper başlatma scripti
# Chrome CDP + clock.py

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE="$HOME/.chrome-sahibinden"
PROJECT_DIR="$(dirname "$0")"

# Chrome zaten çalışıyor mu kontrol et
if ! curl -s http://localhost:9222/json > /dev/null 2>&1; then
    echo "Chrome başlatılıyor..."
    "$CHROME" \
        --remote-debugging-port=9222 \
        --user-data-dir="$PROFILE" \
        --headless=new \
        --no-first-run \
        --no-default-browser-check &
    sleep 3
    echo "Chrome hazır."
else
    echo "Chrome zaten çalışıyor."
fi

echo "Scraper başlatılıyor..."
cd "$PROJECT_DIR"
.venv/bin/python clock.py
