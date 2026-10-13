#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ARCH="$(uname -m)"
case "$ARCH" in
  arm64) LABEL="AppleSilicon" ;;
  x86_64) LABEL="Intel" ;;
  *) echo "Unsupported macOS architecture: $ARCH" >&2; exit 1 ;;
esac

python3 -m pip install -r requirements.txt pyinstaller pillow
brew list ffmpeg >/dev/null 2>&1 || brew install ffmpeg

ICONSET="$ROOT/build/Saeron.iconset"
rm -rf "$ICONSET" "dist/Saeron.app" "portable/Saeron_Portable_macOS_$LABEL"
mkdir -p "$ICONSET"

python3 - "$ROOT/assets/saeron.ico" "$ICONSET" <<'PY'
from pathlib import Path
import sys
from PIL import Image

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
image = Image.open(source).convert("RGBA")
for size in (16, 32, 128, 256, 512):
    image.resize((size, size), Image.Resampling.LANCZOS).save(
        destination / f"icon_{size}x{size}.png"
    )
    if size <= 256:
        image.resize((size * 2, size * 2), Image.Resampling.LANCZOS).save(
            destination / f"icon_{size}x{size}@2x.png"
        )
PY

iconutil -c icns "$ICONSET" -o "$ROOT/assets/saeron.icns"
python3 -m PyInstaller --noconfirm --clean packaging/Saeron_mac.spec

PORTABLE="$ROOT/portable/Saeron_Portable_macOS_$LABEL"
mkdir -p "$PORTABLE"
cp -R "$ROOT/dist/Saeron.app" "$PORTABLE/"
mkdir -p "$PORTABLE/ffmpeg/bin"
cp "$(brew --prefix ffmpeg)/bin/ffmpeg" "$PORTABLE/ffmpeg/bin/"
cp "$ROOT/assets/saeron.icns" "$PORTABLE/"
cp "$ROOT/packaging/PORTABLE_README_MACOS.txt" "$PORTABLE/README.txt"
ditto -c -k --sequesterRsrc --keepParent "$PORTABLE" "$ROOT/portable/Saeron_Portable_macOS_$LABEL.zip"

echo "Created $ROOT/portable/Saeron_Portable_macOS_$LABEL.zip"
