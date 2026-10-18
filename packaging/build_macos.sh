#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != Darwin ]]; then
  echo 'Build BoardBrain.app on macOS. Cross-compiling a Mac app on Linux is not supported.' >&2
  exit 1
fi
python3 -m PyInstaller --noconfirm --clean --windowed --onedir --target-architecture arm64 \
  --name BoardBrain --osx-bundle-identifier com.boardbrain.bench \
  --paths . --add-data 'boardbrain_v2/static:boardbrain_v2/static' \
  --collect-all webview --collect-all keyring --collect-all pillow_heif --collect-data certifi \
  --hidden-import keyring.backends.macOS \
  --exclude-module tkinter --exclude-module pytest \
  packaging/launch.py
codesign --verify --deep --strict dist/BoardBrain.app
ditto -c -k --sequesterRsrc --keepParent dist/BoardBrain.app dist/BoardBrain-macOS-arm64-preview.zip
