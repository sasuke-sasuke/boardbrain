#!/bin/bash
# One-time source build; afterwards launch BoardBrain.app from Finder or the Dock.
set -euo pipefail
cd "$(dirname "$0")"
finish() {
  status=$?
  if [[ $status -ne 0 ]]; then
    echo 'The build stopped. Existing cases and the old BoardBrain installation were not changed.'
    read -r -p 'Press Return to close.' _answer
  fi
}
trap finish EXIT
if [[ "$(uname -s)" != Darwin ]]; then
  echo 'This builder is for macOS.'
  exit 1
fi
if ! command -v python3 >/dev/null || ! python3 -c 'import sys; assert sys.version_info >= (3, 11)' 2>/dev/null; then
  open 'https://www.python.org/downloads/macos/'
  echo 'Install Python 3.11 or newer from python.org, then double-click this builder again.'
  exit 1
fi
echo 'Building BoardBrain once. This may take several minutes.'
python3 -m venv .venv-bench
.venv-bench/bin/python -m pip install -r requirements-build.txt
PATH="$PWD/.venv-bench/bin:$PATH" bash packaging/build_macos.sh
open -R "$PWD/dist/BoardBrain.app"
echo 'Done. Drag BoardBrain.app to Applications, then open it from Finder or the Dock.'
