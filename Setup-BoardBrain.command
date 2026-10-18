#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
umask 077
BB_LOG="$PWD/BoardBrain-Setup.log"
exec > >(tee "$BB_LOG") 2>&1
echo 'BoardBrain setup 0.2.3, revision 5 — schematic image access and source-linked answers.'
finish() {
    status=$?
    if [[ $status -ne 0 ]]; then
        echo
        echo 'Setup stopped. Your saved cases have not been deleted.'
        echo "The details are in: $BB_LOG"
        echo 'Send that log back with the error; never include your API key.'
        read -r -p 'Press Return to close.' _bb_answer || true
    fi
}
trap finish EXIT
if [[ "$(uname -s)" != Darwin ]]; then
    echo 'This setup is for an Apple Silicon Mac.'
    exit 1
fi
if [[ "$(uname -m)" != arm64 ]]; then
    echo 'Open Terminal > Get Info in Finder and turn off Open using Rosetta, then rerun setup on your M-series Mac.'
    exit 1
fi
if ! /usr/bin/xcode-select -p >/dev/null 2>&1; then
    echo 'One-time prerequisite: Apple Command Line Tools are needed to build the Mac app.'
    echo 'Accept the Apple installer, let it finish, then double-click setup again. The full Xcode app is not needed.'
    /usr/bin/xcode-select --install || true
    exit 1
fi
BB_PYTHON=''
# Finder launches may have a shorter PATH than an interactive terminal.
# Check the versioned python.org and Homebrew paths as well as the user's PATH.
BB_CANDIDATES=(
    /Library/Frameworks/Python.framework/Versions/{3.14,3.13,3.12,3.11}/bin/python3
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14
    /opt/homebrew/bin/python3.14 /opt/homebrew/bin/python3.13
    /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11
    /opt/homebrew/opt/python@3.14/bin/python3.14
    /opt/homebrew/bin/python3 /usr/local/bin/python3.14 /usr/local/bin/python3
)
for name in python3.14 python3.13 python3.12 python3.11 python3; do
    BB_CANDIDATE="$(command -v "$name" 2>/dev/null || true)"
    if [[ -n "$BB_CANDIDATE" && "$BB_CANDIDATE" != /usr/bin/python3 ]]; then
        BB_CANDIDATES+=("$BB_CANDIDATE")
    fi
done
for candidate in "${BB_CANDIDATES[@]}"; do
    if [[ ! -x "$candidate" ]]; then continue; fi
    if BB_DETECTED="$("$candidate" -c 'import platform,sys; print("Python",platform.python_version(),platform.machine()); raise SystemExit(0 if (3,11) <= sys.version_info < (3,15) and platform.machine() == "arm64" else 1)' 2>&1)"; then
        BB_PYTHON="$candidate"
        echo "Using $BB_DETECTED at $BB_PYTHON"
        break
    else
        echo "Skipped $candidate: $BB_DETECTED"
    fi
done
if [[ -z "$BB_PYTHON" ]]; then
    echo 'Setup could not find a native Apple Silicon Python 3.11, 3.12, 3.13 or 3.14.'
    echo 'If Python is already installed, send this log so its location or architecture can be checked.'
    echo 'For a new installation, use the Python 3.14 macOS installer at https://www.python.org/downloads/macos/.'
    exit 1
fi
"$BB_PYTHON" - <<'PY'
import fcntl
from pathlib import Path
lock = Path.home() / 'Library/Application Support/BoardBrain/instance.lock'
if lock.exists():
    with lock.open('a+') as file:
        try:
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Quit the running BoardBrain app, then double-click setup again.')
PY
echo '1 of 5 — Preparing an isolated build environment.'
# Avoid mixing packages from an older or differently located virtual environment.
BB_BUILD_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/boardbrain-build.XXXXXX")"
"$BB_PYTHON" -m venv "$BB_BUILD_ROOT/venv"
BB_PYTHON="$BB_BUILD_ROOT/venv/bin/python3"
"$BB_PYTHON" -m pip install --disable-pip-version-check --upgrade pip
"$BB_PYTHON" -m pip install --disable-pip-version-check -r requirements-build.txt

echo '2 of 5 — Checking the app and building the Apple Silicon application.'
"$BB_PYTHON" -m unittest discover -s tests_v2 -q
PATH="$BB_BUILD_ROOT/venv/bin:$PATH" bash packaging/build_macos.sh "$BB_BUILD_ROOT/package"
BB_APP="$BB_BUILD_ROOT/package/dist/BoardBrain.app"

echo '3 of 5 — Checking the packaged app, PDF renderer, photo import and reference search.'
"$BB_PYTHON" tests_v2/smoke_packaged.py "$BB_APP/Contents/MacOS/BoardBrain"

echo '4 of 5 — Preparing your reference workspace.'
if [[ -f reference-bundle/manifest.json ]]; then
    "$BB_APP/Contents/MacOS/BoardBrain" --import-reference "$PWD/reference-bundle/manifest.json"
fi

echo '5 of 5 — Installing and opening BoardBrain.'
BB_APPS="$HOME/Applications"
mkdir -p "$BB_APPS"
BB_STAGE="$(mktemp -d "$BB_APPS/.boardbrain-install.XXXXXX")"
/usr/bin/ditto "$BB_APP" "$BB_STAGE/BoardBrain.app"
"$BB_PYTHON" packaging/macos_bundle.py "$BB_STAGE/BoardBrain.app"
BB_PREVIOUS=''
if [[ -e "$BB_APPS/BoardBrain.app" ]]; then
    BB_PREVIOUS="$BB_APPS/BoardBrain-Previous-$(date +%Y%m%d-%H%M%S).app"
    mv "$BB_APPS/BoardBrain.app" "$BB_PREVIOUS"
fi
if ! mv "$BB_STAGE/BoardBrain.app" "$BB_APPS/BoardBrain.app"; then
    if [[ -n "$BB_PREVIOUS" ]]; then mv "$BB_PREVIOUS" "$BB_APPS/BoardBrain.app"; fi
    exit 1
fi
rmdir "$BB_STAGE"
/usr/bin/open "$BB_APPS/BoardBrain.app"
/usr/bin/open -R "$BB_APPS/BoardBrain.app"
echo
echo 'BoardBrain is installed in your Applications folder.'
echo 'Keep it in the Dock for future launches. Setup is no longer needed for daily use.'
echo 'In BoardBrain, open Settings & equipment to save your OpenAI API key.'
echo 'You can search the reference files before connecting AI.'
echo 'You may close this setup window.'
