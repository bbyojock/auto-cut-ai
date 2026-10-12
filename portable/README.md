# Saeron Portable Windows build

The generated `Saeron_Portable_Windows` folder is the Python-free Windows
distribution. It is created by:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_portable_windows.ps1
```

The same build is available as a GitHub Actions artifact when the
`Build Saeron portable Windows app` workflow is run manually or a `v*` tag is
published. The folder contains the bundled Python runtime, Saeron, and
FFmpeg, so end users can run `Saeron.exe` without installing Python.
