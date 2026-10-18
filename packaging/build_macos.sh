#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != Darwin ]]; then
  echo 'Build BoardBrain.app on macOS. Cross-compiling a Mac app on Linux is not supported.' >&2
  exit 1
fi
# Setup supplies a private temporary directory so build output is not placed in
# Desktop/Documents folders that may receive Finder or file-provider metadata.
BB_OUTPUT="${1:-$PWD}"
mkdir -p "$BB_OUTPUT"
BB_OUTPUT="$(cd "$BB_OUTPUT" && pwd -P)"
mkdir -p "$BB_OUTPUT/spec"
python3 -m PyInstaller --noconfirm --clean --windowed --onedir --target-architecture arm64 \
  --name BoardBrain --osx-bundle-identifier com.boardbrain.bench \
  --distpath "$BB_OUTPUT/dist" --workpath "$BB_OUTPUT/build" --specpath "$BB_OUTPUT/spec" \
  --paths "$PWD" --add-data "$PWD/boardbrain_v2/static:boardbrain_v2/static" \
  --collect-all webview --collect-all keyring --collect-all pillow_heif --collect-data certifi \
  --hidden-import keyring.backends.macOS \
  --exclude-module tkinter --exclude-module pytest \
  "$PWD/packaging/launch.py"
python3 packaging/macos_bundle.py "$BB_OUTPUT/dist/BoardBrain.app"
ditto -c -k --sequesterRsrc --keepParent "$BB_OUTPUT/dist/BoardBrain.app" "$BB_OUTPUT/dist/BoardBrain-macOS-arm64-preview.zip"
