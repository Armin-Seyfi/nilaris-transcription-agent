#!/bin/bash

set -u

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Store persistent user data outside the application code directory.
export NILARIS_DATA_DIR="$HOME/Library/Application Support/Nilaris"

PYTHON="$PROJECT_DIR/.venv/bin/python"
SERVER="$PROJECT_DIR/src/web_server.py"
LOG_DIR="$HOME/Library/Logs/Nilaris"
LOG_FILE="$LOG_DIR/nilaris-server.log"
URL="http://127.0.0.1:8765"
HEALTH_URL="$URL/api/health"

show_error() {
    echo "Nilaris Launcher Error: $1" >&2
    exit 1
}

check_health() {
    /usr/bin/curl --silent --max-time 2 "$HEALTH_URL" |
        /usr/bin/grep -q '"application":"Nilaris"'
}

# Validate required application files.
[ -x "$PYTHON" ] ||
    show_error "Python environment not found: $PYTHON"

[ -f "$SERVER" ] ||
    show_error "Flask server not found: $SERVER"

# Start Flask only if Nilaris is not already running.
if ! check_health; then
    mkdir -p "$LOG_DIR"

    cd "$PROJECT_DIR" || exit 1

    nohup "$PYTHON" "$SERVER" \
        >> "$LOG_FILE" 2>&1 < /dev/null &

    # Wait for Flask to become available.
    for attempt in {1..30}; do
        if check_health; then
            break
        fi

        sleep 1
    done
fi

if ! check_health; then
    show_error "Server did not become ready. Check logs."
fi

# Open Nilaris in Google Chrome.
/usr/bin/open -a "Google Chrome" "$URL"

echo "Nilaris is ready at $URL"
