$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BuildRoot = Join-Path $Root "build"
$DistRoot = Join-Path $Root "dist"
$PortableRoot = Join-Path $Root "portable\Saeron_Portable_Windows"
$Python = if ($env:PYTHON) { $env:PYTHON } else { "python" }

Push-Location $Root
try {
    & $Python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Application dependency installation failed." }
    & $Python -m pip install --upgrade pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller installation failed." }

    Remove-Item $BuildRoot -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $DistRoot -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $PortableRoot -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $PortableRoot -Force | Out-Null

    & $Python -m PyInstaller --noconfirm --clean packaging\Saeron.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

    Copy-Item (Join-Path $DistRoot "Saeron\*") $PortableRoot -Recurse -Force
    Copy-Item (Join-Path $Root "assets\saeron.ico") (Join-Path $PortableRoot "saeron.ico") -Force
    Copy-Item (Join-Path $Root "packaging\PORTABLE_README.txt") (Join-Path $PortableRoot "README.txt") -Force

    $FfmpegZip = Join-Path $env:TEMP "saeron-ffmpeg.zip"
    $FfmpegExtract = Join-Path $env:TEMP "saeron-ffmpeg"
    if (-not (Test-Path (Join-Path $PortableRoot "ffmpeg\bin\ffmpeg.exe"))) {
        Invoke-WebRequest `
            -Uri "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" `
            -OutFile $FfmpegZip
        Remove-Item $FfmpegExtract -Recurse -Force -ErrorAction SilentlyContinue
        Expand-Archive $FfmpegZip -DestinationPath $FfmpegExtract -Force
        $FfmpegBin = Get-ChildItem $FfmpegExtract -Directory | ForEach-Object {
            Join-Path $_.FullName "bin"
        } | Where-Object { Test-Path (Join-Path $_ "ffmpeg.exe") } | Select-Object -First 1
        if (-not $FfmpegBin) { throw "The downloaded FFmpeg archive did not contain bin\ffmpeg.exe." }
        New-Item -ItemType Directory -Path (Join-Path $PortableRoot "ffmpeg\bin") -Force | Out-Null
        Copy-Item (Join-Path $FfmpegBin "*") (Join-Path $PortableRoot "ffmpeg\bin") -Recurse -Force
    }

    $Launcher = Join-Path $PortableRoot "Start Saeron.bat"
    @'
@echo off
setlocal
cd /d "%~dp0"
set "PATH=%~dp0ffmpeg\bin;%PATH%"
start "" "%~dp0Saeron.exe"
'@ | Set-Content -Path $Launcher -Encoding ascii

    Write-Host "Portable build created at $PortableRoot"
}
finally {
    Pop-Location
}
