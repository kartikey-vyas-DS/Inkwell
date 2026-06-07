#!/bin/bash
set -e

# ── Colours ────────────────────────────────────────────────────────────────────
CYAN='\033[0;36m'; GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'

echo ""
echo -e "${CYAN}============================================================${NC}"
echo -e "${CYAN}  Inkwell — One-Time Setup (Mac/Linux)${NC}"
echo -e "${CYAN}============================================================${NC}"
echo ""
echo "  This will:"
echo "    1. Check / install Python 3.11+"
echo "    2. Create a virtual environment"
echo "    3. Install all dependencies"
echo "    4. Create a launch script"
echo ""
read -p "  Press Enter to continue..."

# ── Step 1: Check Python ────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[1/4] Checking Python...${NC}"

PYTHON=""
for cmd in python3.11 python3.12 python3.10 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        if "$cmd" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>/dev/null; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo -e "${YELLOW}  Python 3.11+ not found. Attempting to install...${NC}"

    # Mac: try Homebrew
    if [[ "$OSTYPE" == "darwin"* ]]; then
        if command -v brew &>/dev/null; then
            echo "  Using Homebrew to install Python 3.11..."
            brew install python@3.11
            PYTHON=$(brew --prefix)/bin/python3.11
        else
            echo ""
            echo -e "${RED}  [ERROR] Homebrew not found.${NC}"
            echo "  Please install Python 3.11 from: https://python.org/downloads"
            echo "  Then run this script again."
            exit 1
        fi
    # Linux: try apt
    elif command -v apt-get &>/dev/null; then
        echo "  Using apt to install Python 3.11..."
        sudo apt-get update -qq
        sudo apt-get install -y python3.11 python3.11-venv python3-pip
        PYTHON=python3.11
    else
        echo ""
        echo -e "${RED}  [ERROR] Cannot auto-install Python.${NC}"
        echo "  Please install Python 3.11+ manually, then run this script again."
        exit 1
    fi
fi

PYVER=$("$PYTHON" --version)
echo -e "  ${GREEN}Found: $PYVER${NC}"

# ── Step 2: Virtual environment ─────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[2/4] Creating virtual environment...${NC}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"

if [ -d "$VENV_DIR" ]; then
    echo "  Virtual environment already exists, skipping."
else
    "$PYTHON" -m venv "$VENV_DIR"
    echo -e "  ${GREEN}Created: $VENV_DIR${NC}"
fi

# ── Step 3: Install dependencies ─────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[3/4] Installing dependencies (3-5 minutes)...${NC}"
echo "  Please wait — downloading packages..."
echo ""

source "$VENV_DIR/bin/activate"
pip install --upgrade pip --quiet
pip install -r "$SCRIPT_DIR/requirements.txt" --quiet

echo -e "  ${GREEN}All dependencies installed.${NC}"

# ── Step 4: Create launch alias / shortcut ────────────────────────────────────────
echo ""
echo -e "${CYAN}[4/4] Setting up launcher...${NC}"

# Make start.sh executable
chmod +x "$SCRIPT_DIR/start.sh"

# Add alias to shell profile on Mac
if [[ "$OSTYPE" == "darwin"* ]]; then
    PROFILE=""
    if [ -f "$HOME/.zshrc" ]; then PROFILE="$HOME/.zshrc"
    elif [ -f "$HOME/.bash_profile" ]; then PROFILE="$HOME/.bash_profile"
    elif [ -f "$HOME/.bashrc" ]; then PROFILE="$HOME/.bashrc"
    fi

    if [ -n "$PROFILE" ]; then
        ALIAS_LINE="alias Inkwell='$SCRIPT_DIR/start.sh'"
        if ! grep -q "Inkwell" "$PROFILE" 2>/dev/null; then
            echo "" >> "$PROFILE"
            echo "# Inkwell" >> "$PROFILE"
            echo "$ALIAS_LINE" >> "$PROFILE"
            echo -e "  ${GREEN}Added 'Inkwell' alias to $PROFILE${NC}"
        else
            echo "  Alias already exists in $PROFILE"
        fi
    fi

    # Create a Mac app launcher in Applications (optional, best-effort)
    APP_DIR="$HOME/Applications/Inkwell.app/Contents/MacOS"
    if mkdir -p "$APP_DIR" 2>/dev/null; then
        cat > "$APP_DIR/Inkwell" << EOF
#!/bin/bash
cd "$SCRIPT_DIR"
"$SCRIPT_DIR/start.sh"
EOF
        chmod +x "$APP_DIR/Inkwell"
        echo -e "  ${GREEN}Created app in ~/Applications/Inkwell.app${NC}"
    fi
fi

# ── Done ─────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}  Setup Complete!${NC}"
echo -e "${GREEN}============================================================${NC}"
echo ""
echo "  Next steps:"
echo ""
echo "    1. Copy .env.template to .env and add your API keys"
echo "       (or the browser setup wizard will guide you)"
echo ""
echo "    2. Launch the app:"
echo "       ./start.sh"
if [[ "$OSTYPE" == "darwin"* ]]; then
echo "       — or type: Inkwell  (after opening a new terminal)"
echo "       — or double-click: ~/Applications/Inkwell.app"
fi
echo ""
echo "    3. Put your PDF books in the Books folder"
echo "       (the browser UI has a Books tab to upload them)"
echo ""
echo -e "${GREEN}============================================================${NC}"
echo ""
