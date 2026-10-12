Saeron Portable for Windows
===========================

Run "Saeron.exe" or double-click "Start Saeron.bat".
Python is included; no Python installation is required.

The bundled FFmpeg files are used for audio extraction.
AI provider API keys are saved in the config folder next to the application.
Whisper models are downloaded by faster-whisper on first use and require
an internet connection.

Portable build command:
  powershell -ExecutionPolicy Bypass -File packaging\build_portable_windows.ps1
