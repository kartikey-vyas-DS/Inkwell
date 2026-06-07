#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'

if [ ! -f "$VENV_DIR/bin/activate" ]; then
    echo -e "${RED}  [ERROR] Setup not complete. Run: ./install.sh${NC}"
    exit 1
fi

# ── Check if already running ───────────────────────────────────────────────
if lsof -Pi :8000 -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo ""
    echo "  Inkwell is already running — opening browser."
    if [[ "$OSTYPE" == "darwin"* ]]; then open "http://localhost:8000"
    elif command -v xdg-open &>/dev/null; then xdg-open "http://localhost:8000"
    fi
    exit 0
fi

source "$VENV_DIR/bin/activate"
cd "$SCRIPT_DIR"

# Open browser after 6 seconds (extra time for slow machines / antivirus)
(
    sleep 6
    if [[ "$OSTYPE" == "darwin"* ]]; then open "http://localhost:8000"
    elif command -v xdg-open &>/dev/null; then xdg-open "http://localhost:8000"
    fi
) &

echo ""
echo -e "${CYAN}============================================================${NC}"
echo -e "${CYAN}  Inkwell${NC}"
echo -e "${CYAN}============================================================${NC}"
echo ""
echo "  Starting server — browser opens in a moment."
echo "  To stop: press Ctrl+C"
echo -e "${CYAN}============================================================${NC}"
echo ""

python app.py