#!/usr/bin/env bash
# TAU one-command installer for Linux (and macOS, without the menu entry).
#
#   curl -fsSL <raw URL of this file> | bash
#   bash install.sh                 # from a TAU checkout
#
# What it does, in order. Each step is skipped if it's already done, so it's
# safe to run again.
#   1. Gets a private copy of uv and Python 3.12 (nothing system-wide, no sudo).
#   2. Makes TAU's own Python environment and installs OpenJarvis from PyPI.
#      Nothing is compiled: no Rust, no OpenJarvis installer, no Ollama.
#   3. Installs TAU (with the CPU-only build of torch).
#   4. Installs Kokoro and misaki without their GPL extras.
#   5. Downloads the voice and hearing models up front.
#   6. Adds the `tau` command (~/.local/bin) and a TAU menu entry.
#   7. Starts `tau setup`, one question at a time.
#
# Everything the tools print goes to a log file; the screen gets plain lines.
#
# Flags:
#   --dry-run                    say what would run, change nothing
#   --no-setup                   stop before `tau setup`
#   --no-voice                   text only
#   --no-models                  don't download the voice and hearing models now
#   --use-openjarvis-installer   use OpenJarvis's own installer and environment instead
#   --uninstall                  remove TAU's environment, command and menu entry (settings stay)
#
# Environment:
#   TAU_INSTALL_DIR           where TAU's environment goes (default: ~/.local/share/tau)
#   TAU_SOURCE                a TAU source folder (default: the folder holding this script,
#                             or a download of TAU_SOURCE_TARBALL)
#   OPENJARVIS_HOME           where OpenJarvis and TAU keep settings
#   TAU_OPENJARVIS_INSTALLER  only with --use-openjarvis-installer

set -uo pipefail

DRY=0; SETUP=1; VOICE=1; MODELS=1; OJI=0; UNINSTALL=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY=1 ;;
        --no-setup) SETUP=0 ;;
        --no-voice) VOICE=0 ;;
        --no-models) MODELS=0 ;;
        --use-openjarvis-installer) OJI=1 ;;
        --uninstall) UNINSTALL=1 ;;
        --minimal) ;;   # kept for old instructions; nothing downloads a model now
        -h|--help) sed -n '2,38p' "$0" 2>/dev/null; exit 0 ;;
        *) echo "install.sh: I don't know the option '$arg'. Run with --help to see the options."; exit 2 ;;
    esac
done

# Measured on a clean GitHub ubuntu-latest runner (see results/tau_build_notes.md, Round 3).
EST_MIN="3 minutes on a fast connection, up to 15 on a slow one,"
EST_DL="about 1.8 GB"
TOTAL=7

INSTALL_DIR="${TAU_INSTALL_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/tau}"
VENV="$INSTALL_DIR/venv"
TOOLS="$INSTALL_DIR/tools"
if [ "$OJI" -eq 1 ]; then VENV="${OPENJARVIS_HOME:-$HOME/.openjarvis}/.venv"; fi
OJ_INSTALLER="${TAU_OPENJARVIS_INSTALLER:-https://open-jarvis.github.io/OpenJarvis/install.sh}"
TARBALL="${TAU_SOURCE_TARBALL:-https://codeload.github.com/RAnthonyBeckett/tau/tar.gz/refs/heads/main}"
PY="$VENV/bin/python"
LOG="$INSTALL_DIR/logs/install-$(date +%Y%m%d-%H%M%S).log"
START=$(date +%s)
if [ "$DRY" -eq 0 ]; then mkdir -p "$INSTALL_DIR/logs"; echo "TAU install log" > "$LOG"; fi

say()  { printf '%s\n' "$*"; }
step() { say ""; say "[$1/$TOTAL] $2"; }
log()  { [ "$DRY" -eq 1 ] || printf '%s\n' "$*" >> "$LOG"; }
fail() {
    say ""; say "Stopped: $1"; say "Next: $2"
    [ "$DRY" -eq 1 ] || say "The details are in $LOG"
    exit 1
}
human() { local s=$1; if [ "$s" -lt 60 ]; then echo "$s seconds"; else echo "about $(( (s + 30) / 60 )) minutes"; fi; }

# Run a command with its output in the log, saying what's happening every 15 seconds.
# usage: logged "<what it's doing>" "<typical time or empty>" cmd args...
logged() {
    local label="$1" typical="$2"; shift 2
    if [ "$DRY" -eq 1 ]; then say "    would run: $*"; return 0; fi
    log ""; log ">>> $*"
    local t0; t0=$(date +%s)
    "$@" >> "$LOG" 2>&1 &
    local pid=$! next=15
    while kill -0 "$pid" 2>/dev/null; do
        sleep 1
        local s=$(( $(date +%s) - t0 ))
        if [ "$s" -ge "$next" ]; then
            next=$(( next + 15 ))
            if [ -n "$typical" ]; then say "    still $label ($(human "$s") so far; this usually takes $typical)"
            else say "    still $label ($(human "$s") so far)"; fi
        fi
    done
    wait "$pid"; local rc=$?
    log "<<< exit $rc after $(( $(date +%s) - t0 )) s"
    return $rc
}
shown() {   # TAU's own commands: their screen lines are meant for the user; stderr goes to the log
    if [ "$DRY" -eq 1 ]; then say "    would run: $*"; return 0; fi
    log ">>> $*"; "$@" 2>> "$LOG"
}
test_py() { [ "$DRY" -eq 0 ] && [ -x "$PY" ] && "$PY" -c "$1" >> "${LOG:-/dev/null}" 2>&1; }
fetch() {   # url dest
    if [ "$DRY" -eq 1 ]; then say "    would download: $1"; return 0; fi
    if command -v curl >/dev/null 2>&1; then curl -fsSL --max-time 300 "$1" -o "$2" 2>> "$LOG"
    elif command -v wget >/dev/null 2>&1; then wget -q -T 300 -O "$2" "$1" 2>> "$LOG"
    elif command -v python3 >/dev/null 2>&1; then python3 -c 'import sys, urllib.request as u; u.urlretrieve(sys.argv[1], sys.argv[2])' "$1" "$2" 2>> "$LOG"
    else return 1; fi
}

# ---------------------------------------------------------------- uninstall
if [ "$UNINSTALL" -eq 1 ]; then
    say "Removing TAU from this computer. Your settings and TAU's notes stay where they are."
    [ -x "$PY" ] && shown "$PY" -m tau.launcher remove
    if [ "$DRY" -eq 1 ]; then say "    would remove: $VENV $TOOLS"; exit 0; fi
    rm -rf "$VENV" "$TOOLS" "$INSTALL_DIR/src" "$INSTALL_DIR/python"
    say "Done. The tau command and the menu entry are gone."
    exit 0
fi

say "Installing TAU, a voice-first lab assistant."
say "This takes about $EST_MIN and downloads $EST_DL. I'll say what's happening the whole way."
[ "$DRY" -eq 1 ] || say "Everything the tools print goes to $LOG"

# ---------------------------------------------------------------- 1 uv and Python
step 1 "Python tools (a private copy of uv and Python 3.12, about 50 MB)"
UV=""
[ -x "$PY" ] && say "    TAU's Python environment is already here: $VENV"
if command -v uv >/dev/null 2>&1; then UV="$(command -v uv)"
elif [ -x "$TOOLS/uv" ]; then UV="$TOOLS/uv"
elif [ "$OJI" -eq 0 ]; then
    case "$(uname -s)-$(uname -m)" in
        Linux-x86_64) triple="x86_64-unknown-linux-gnu" ;;
        Linux-aarch64|Linux-arm64) triple="aarch64-unknown-linux-gnu" ;;
        Darwin-arm64) triple="aarch64-apple-darwin" ;;
        Darwin-x86_64) triple="x86_64-apple-darwin" ;;
        *) triple="" ;;
    esac
    if [ -n "$triple" ]; then
        say "    downloading uv, a fast Python package tool (about 20 MB)"
        tgz="$(mktemp 2>/dev/null || echo /tmp/tau-uv.tgz)"
        if fetch "https://github.com/astral-sh/uv/releases/latest/download/uv-$triple.tar.gz" "$tgz"; then
            if [ "$DRY" -eq 1 ]; then UV="$TOOLS/uv"
            else
                mkdir -p "$TOOLS"
                if tar -xzf "$tgz" -C "$TOOLS" --strip-components=1 2>> "$LOG" && [ -x "$TOOLS/uv" ]; then UV="$TOOLS/uv"; fi
            fi
        fi
    fi
    [ -n "$UV" ] || say "    couldn't get uv; I'll use this computer's own Python instead"
fi
[ -n "$UV" ] && [ "$DRY" -eq 0 ] && export UV_PYTHON_INSTALL_DIR="$INSTALL_DIR/python"

pip_install() {   # label typical args...
    local label="$1" typical="$2"; shift 2
    if [ -n "$UV" ]; then logged "$label" "$typical" "$UV" pip install --python "$PY" "$@"
    else logged "$label" "$typical" "$PY" -m pip install --disable-pip-version-check "$@"; fi
}

# ---------------------------------------------------------------- 2 OpenJarvis
step 2 "OpenJarvis (from PyPI: plain Python, nothing to compile)"
if [ "$OJI" -eq 1 ]; then
    if test_py "import openjarvis"; then say "    already installed in $VENV"
    else
        say "    running OpenJarvis's own installer (it compiles Rust and sets up a model; this can take 30 minutes)"
        installer_file="$OJ_INSTALLER"
        if [ ! -f "$installer_file" ]; then
            installer_file="$(mktemp)"
            fetch "$OJ_INSTALLER" "$installer_file" || fail "I couldn't download OpenJarvis's installer." "Run this script again without --use-openjarvis-installer."
        fi
        shown bash "$installer_file" --no-analytics --no-bg-orchestrator \
            || fail "OpenJarvis's installer didn't finish." "Run this script again without --use-openjarvis-installer."
    fi
else
    if [ ! -x "$PY" ]; then
        say "    making TAU's own Python environment in $VENV"
        if [ -n "$UV" ]; then
            logged "making the environment" "1 minute" "$UV" venv --seed --python 3.12 "$VENV" \
                || fail "I couldn't make a Python environment." "Check your internet connection, then run this script again."
        else
            base=""
            for cand in python3.12 python3.11 python3.13 python3.10 python3; do
                if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys, venv; sys.exit(0 if (3,10) <= sys.version_info[:2] < (3,14) else 1)' 2>/dev/null; then
                    base="$cand"; break
                fi
            done
            [ -n "$base" ] || fail "I couldn't get uv, and there's no Python 3.10 to 3.13 here." "Install Python 3.12 (with its venv module), then run this script again."
            logged "making the environment" "" "$base" -m venv "$VENV" \
                || fail "I couldn't make a Python environment in $VENV." "Install your system's python3-venv package, then run this script again."
        fi
    fi
    if test_py "import openjarvis"; then say "    OpenJarvis is already installed"
    else
        pip_install "installing OpenJarvis" "1 to 2 minutes" openjarvis \
            || fail "Installing OpenJarvis from PyPI didn't work." "Check your internet connection, then run this script again."
    fi
fi
if [ "$DRY" -eq 0 ] && ! test_py "import openjarvis"; then
    fail "OpenJarvis isn't working in $VENV." "Run this script again; if it stops here twice, send the log to whoever gave you TAU."
fi
say "    OpenJarvis is ready"

# ---------------------------------------------------------------- 3 TAU
step 3 "TAU"
SRC="${TAU_SOURCE:-}"
if [ -z "$SRC" ]; then
    here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
    if [ -n "$here" ] && [ -f "$here/pyproject.toml" ] && grep -q '^name = "tau"' "$here/pyproject.toml"; then
        SRC="$here"
    else
        say "    downloading TAU (about 5 MB)"
        tb="$(mktemp 2>/dev/null || echo /tmp/tau-src.tgz)"
        fetch "$TARBALL" "$tb" || fail "I couldn't download TAU." "Check your internet connection, then run this script again."
        if [ "$DRY" -eq 1 ]; then SRC="$INSTALL_DIR/src"
        else
            rm -rf "$INSTALL_DIR/src"; mkdir -p "$INSTALL_DIR/src"
            tar -xzf "$tb" -C "$INSTALL_DIR/src" --strip-components=1 2>> "$LOG" || fail "The TAU download was damaged." "Run this script again."
            SRC="$INSTALL_DIR/src"
            [ -f "$SRC/pyproject.toml" ] || SRC="$INSTALL_DIR/src/tau"   # older archives kept TAU in a tau/ folder
        fi
    fi
fi
[ "$DRY" -eq 1 ] || [ -f "$SRC/pyproject.toml" ] || fail "There's no TAU source at $SRC." "Set TAU_SOURCE to the tau folder, or unset it, then run this script again."
extras="display"
[ "$VOICE" -eq 1 ] && extras="voice,display"
test_py "import tau" && say "    TAU is already installed; updating it"
if [ "$VOICE" -eq 1 ] && [ "$(uname -s)" = "Linux" ] && ! test_py "import torch"; then
    # The CPU-only torch build avoids NVIDIA's proprietary CUDA libraries (TAU runs voice on the CPU).
    say "    installing torch, the maths library the voice uses (CPU-only build, about 200 MB)"
    pip_install "installing torch" "2 to 4 minutes" torch --index-url https://download.pytorch.org/whl/cpu \
        || say "    (the CPU-only build wasn't reachable; the normal build will come with TAU)"
fi
say "    installing TAU and the packages it needs (about 300 MB)"
if [ -n "$UV" ]; then
    logged "installing TAU" "2 to 5 minutes" "$UV" pip install --python "$PY" --reinstall-package tau "$SRC[$extras]"
else
    logged "installing TAU" "2 to 5 minutes" "$PY" -m pip install --disable-pip-version-check "$SRC[$extras]"
fi || fail "Installing TAU didn't work." "Check your internet connection, then run this script again."
say "    TAU is installed"

# ---------------------------------------------------------------- 4 Kokoro
step 4 "Voice packages (Kokoro, without GPL extras)"
if [ "$VOICE" -eq 0 ]; then
    say "    skipped (text only)"
elif test_py "import importlib.util as u, sys; sys.exit(0 if all(u.find_spec(m) for m in ('kokoro', 'misaki', 'en_core_web_sm')) else 1)"; then
    say "    already installed"
else
    knext="Run this script again, or add --no-voice to finish without a voice."
    if test_py 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,13) else 1)'; then
        # kokoro and misaki declare Python <3.13 but run on it; only pip can be told to ignore that.
        logged "installing Kokoro" "1 minute" "$PY" -m pip install --ignore-requires-python --no-deps -r "$SRC/requirements-kokoro.txt" \
            || fail "Installing Kokoro didn't work." "$knext"
    else
        pip_install "installing Kokoro" "1 minute" --no-deps -r "$SRC/requirements-kokoro.txt" || fail "Installing Kokoro didn't work." "$knext"
    fi
    say "    Kokoro is installed"
fi

# ---------------------------------------------------------------- 5 models
step 5 "Voice and hearing models (downloaded now, so nothing downloads mid-conversation)"
if [ "$VOICE" -eq 0 ] || [ "$MODELS" -eq 0 ]; then say "    skipped"
elif ! shown "$PY" -m tau fetch-models; then
    say "    Some models didn't download. TAU still works by typing; run 'tau fetch-models' later to try again."
fi

# ---------------------------------------------------------------- 6 launcher
step 6 "The tau command and a menu entry"
shown "$PY" -m tau.launcher install || say "    I couldn't add the menu entry. You can still start TAU with: $PY -m tau run"

say ""
say "TAU is installed ($(human $(( $(date +%s) - START )))). Open it from your apps menu (TAU), or type: tau run"
log "install finished in $(( $(date +%s) - START )) s"

# ---------------------------------------------------------------- 7 setup
TAU_BIN="$VENV/bin/tau"
step 7 "Setup"
if [ "$SETUP" -eq 0 ]; then say "    skipped. Run 'tau setup' when you're ready."; exit 0; fi
say "    starting tau setup"
if [ "$DRY" -eq 1 ]; then say "    would run: $TAU_BIN setup"; exit 0; fi
# Read answers from the terminal even when this script itself was piped in.
if [ -t 0 ] || [ ! -r /dev/tty ] || [ "${TAU_SETUP_STDIN:-0}" = "1" ]; then
    exec "$TAU_BIN" setup
else
    exec "$TAU_BIN" setup < /dev/tty
fi
