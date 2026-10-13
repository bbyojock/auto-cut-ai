# PyInstaller one-folder build for Linux.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all


root = Path(SPECPATH).parent
datas = [(str(root / "assets"), "assets")]
binaries = []
hiddenimports = []

for package in ("customtkinter", "faster_whisper", "ctranslate2", "av"):
    try:
        package_datas, package_binaries, package_hiddenimports = collect_all(package)
    except ImportError:
        continue
    datas.extend(package_datas)
    binaries.extend(package_binaries)
    hiddenimports.extend(package_hiddenimports)


a = Analysis(
    [str(root / "app.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "notebook", "jupyter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Saeron",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Saeron",
)
