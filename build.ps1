$ErrorActionPreference = "Stop"

pyinstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name AFKPilot `
    --distpath dist `
    --workpath build\pyinstaller `
    --specpath build `
    AFKPilot.py
