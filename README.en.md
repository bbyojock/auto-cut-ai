# Saeron

- [한국어 README](README.md)

Saeron is an AI-assisted video editing tool. It analyzes footage, transcripts,
visual activity, and editing preferences to create an editable rough-cut plan
that can be reviewed or exported to DaVinci Resolve.

## Main features

- Automatic rough-cut planning for long videos
- Transcript and visual-activity analysis
- Folder-based footage analysis
- Multicam grouping and angle-switch planning
- Separate Film / Drama mode for scripts, storyboards, shooting plans, and takes
- DaVinci Resolve XML export
- Multiple AI provider configuration

## Portable downloads

Portable builds include the runtime and media-processing components needed to
start Saeron without installing Python separately.

| Platform | Download |
|---|---|
| Windows 64-bit | [Saeron Portable for Windows](https://drive.google.com/file/d/1ZOnPMu17YDsXNUyELPOZSUZ69iAY12Fq/view?usp=sharing) |
| macOS Apple Silicon | [Saeron Portable for macOS Apple Silicon](https://drive.google.com/file/d/1O-URPNqsDxN6ejm4c-SnnA5yp0yPGYt6/view?usp=sharing) |
| macOS Intel | [Saeron Portable for macOS Intel](https://drive.google.com/file/d/1DVp486IPcMMETWoWybMPxR6Bfa9cwUGm/view?usp=sharing) |
| Linux 64-bit | Coming soon |

### Windows

1. Download the ZIP file and extract it.
2. Run `Saeron.exe` or `Start Saeron.bat`.
3. Open Settings and enter an AI provider and API key.

### macOS Apple Silicon / Intel

1. Download the build for your Mac architecture and extract it.
2. Move `Saeron.app` to Applications or another folder.
3. On the first launch, Control-click `Saeron.app`, choose **Open**, and
   confirm if macOS displays a security warning.
4. Configure the AI provider and API key in Settings.

### Linux 64-bit

The Linux portable build is coming soon. Once available, extract the archive
and run:

```bash
chmod +x Saeron
./Saeron
```

## Film / Drama mode

Film / Drama mode is separate from the general automatic editor. Script,
storyboard, footage, and shooting-plan inputs are optional; you can provide
only the materials available for a project and add the rest later. Saeron does
not require one fixed screenplay or storyboard template.

Supported project inputs include:

- TXT screenplay files with common scene headings
- Storyboard image folders
- Footage folders with scene/take information in filenames
- TXT or CSV shooting plans

Example filenames:

```text
scene_1_take_2.mp4
scene_1_panel_1.png
```

## Configuration and requirements

Portable builds do not require a separate Python installation. AI features
require an internet connection and a configured provider API key. Whisper
models may be downloaded on first use. DaVinci Resolve integration requires
DaVinci Resolve to be installed.

Settings and API keys are stored in the portable application's `config`
directory when it is writable. If the application is placed in a protected
directory, Saeron uses the operating system's user application-data directory.

## Developer builds

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_portable_windows.ps1
```

macOS:

```bash
bash packaging/build_portable_macos.sh
```

Linux:

```bash
bash packaging/build_portable_linux.sh
```

The GitHub Actions workflows build Windows, macOS Apple Silicon, macOS Intel,
and Linux artifacts separately.
