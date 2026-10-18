#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
umask 077
BB_LOG="$PWD/BoardBrain-Setup.log"
exec > >(tee "$BB_LOG") 2>&1
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
for candidate in /Library/Frameworks/Python.framework/Versions/{3.13,3.12,3.11}/bin/python3 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if [[ -x "$candidate" ]] && "$candidate" -c 'import platform,sys; assert (3,11) <= sys.version_info < (3,14) and platform.machine() == "arm64"' 2>/dev/null; then
        BB_PYTHON="$candidate"
        break
    fi
done
if [[ -z "$BB_PYTHON" ]]; then
    echo 'One-time prerequisite: install Python 3.13 using its macOS installer.'
    echo 'The official download page is opening. After installation, double-click Setup-BoardBrain.command again.'
    /usr/bin/open 'https://www.python.org/downloads/release/python-31316/'
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
"$BB_PYTHON" -m pip install --disable-pip-version-check -r requirements-build.txt

echo '2 of 5 — Checking the app and building the Apple Silicon application.'
"$BB_PYTHON" -m unittest discover -s tests_v2 -q
PATH="$BB_BUILD_ROOT/venv/bin:$PATH" bash packaging/build_macos.sh

echo '3 of 5 — Checking the packaged app, PDF renderer, photo import and reference search.'
"$BB_PYTHON" tests_v2/smoke_packaged.py "$PWD/dist/BoardBrain.app/Contents/MacOS/BoardBrain"

echo '4 of 5 — Preparing your reference workspace.'
if [[ -f reference-bundle/manifest.json ]]; then
    "$PWD/dist/BoardBrain.app/Contents/MacOS/BoardBrain" --import-reference "$PWD/reference-bundle/manifest.json"
fi

echo '5 of 5 — Installing and opening BoardBrain.'
BB_APPS="$HOME/Applications"
mkdir -p "$BB_APPS"
BB_STAGE="$(mktemp -d "$BB_APPS/.boardbrain-install.XXXXXX")"
/usr/bin/ditto "$PWD/dist/BoardBrain.app" "$BB_STAGE/BoardBrain.app"
/usr/bin/codesign --verify --deep --strict "$BB_STAGE/BoardBrain.app"
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
