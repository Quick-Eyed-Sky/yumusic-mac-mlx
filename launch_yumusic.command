#!/bin/zsh
#
# YuMusic - double-click launcher.
#
# You do not need to edit this file. It works out where it is, finds the
# YuE2 model repo, starts the app and opens it in your browser. If the model
# lives somewhere unusual on your machine, set YUE_REPO before running it -
# see INSTALL.md.
#
# To close the app: close the Terminal window this opens, or press
# Control-C in it.

# --- where things are -------------------------------------------------------

# The folder this launcher is sitting in. This is how the app can be moved
# anywhere on the disk and still start.
HERE="${0:A:h}"
APP="$HERE/yumusic2_app.py"

# Where the YuE2-3B-MLX model repo is installed. INSTALL.md puts it here.
REPO="${YUE_REPO:-$HOME/YuE/YuE2-3B-MLX}"
VENV="$HOME/YuE/venv/bin/activate"

PORT="${YUMUSIC2_PORT:-7870}"
URL="http://127.0.0.1:$PORT"

export YUE_REPO="$REPO"

# --- checks, with a readable message for each failure -----------------------

if [ ! -f "$APP" ]; then
  echo "Can't find yumusic2_app.py next to this launcher."
  echo "Looked for: $APP"
  echo ""
  echo "Keep launch_yumusic.command and yumusic2_app.py in the same folder."
  read -n 1 -s -r -p "Press any key to close this window..."
  exit 1
fi

if [ ! -f "$VENV" ]; then
  echo "Can't find the Python environment for YuE2."
  echo "Looked for: $VENV"
  echo ""
  echo "That means the model and its environment are not installed yet, or"
  echo "they are installed somewhere else on this Mac."
  echo ""
  echo "  - Not installed?  Open INSTALL.md in this folder and follow it once."
  read -n 1 -s -r -p "Press any key to close this window..."
  exit 1
fi

if [ ! -d "$REPO" ]; then
  echo "Can't find the YuE2-3B-MLX model repo."
  echo "Looked for: $REPO"
  echo ""
  echo "  - Not installed?  Open INSTALL.md in this folder and follow it once."
  echo "  - Installed elsewhere?  Tell this launcher where, like this:"
  echo "        export YUE_REPO=/your/path/to/YuE2-3B-MLX"
  read -n 1 -s -r -p "Press any key to close this window..."
  exit 1
fi

# Already running? Just bring it back up rather than starting a second copy.
if lsof -i tcp:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  echo "YuMusic is already running on port $PORT."
  echo "Reopening it in your browser."
  open "$URL"
  read -n 1 -s -r -p "Press any key to close this window..."
  exit 0
fi

# --- go ---------------------------------------------------------------------

source "$VENV"
cd "$HERE"

echo ""
echo "  YuMusic"
echo "  ----------------------------------------------------------------"
echo "  Starting up. This window is the log: keep it open while you work."
echo "  Your browser will open in a few seconds at $URL"
echo "  ----------------------------------------------------------------"
echo ""

(sleep 4 && open "$URL") &

python3 "$APP"

echo ""
read -n 1 -s -r -p "YuMusic has stopped. Press any key to close this window..."
