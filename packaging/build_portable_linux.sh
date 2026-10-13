#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

python3 -m pip install -r requirements.txt pyinstaller
sudo apt-get update
sudo apt-get install -y ffmpeg

rm -rf build dist "portable/Saeron_Portable_Linux_x64"
python3 -m PyInstaller --noconfirm --clean packaging/Saeron_linux.spec

PORTABLE="$ROOT/portable/Saeron_Portable_Linux_x64"
mkdir -p "$PORTABLE/ffmpeg/bin"
cp -R "$ROOT/dist/Saeron/"* "$PORTABLE/"
cp "$(command -v ffmpeg)" "$PORTABLE/ffmpeg/bin/ffmpeg"
cp "$ROOT/packaging/PORTABLE_README_LINUX.txt" "$PORTABLE/README.txt"
chmod +x "$PORTABLE/Saeron"
tar -czf "$ROOT/portable/Saeron_Portable_Linux_x64.tar.gz" -C "$ROOT/portable" "Saeron_Portable_Linux_x64"

echo "Created $ROOT/portable/Saeron_Portable_Linux_x64.tar.gz"
