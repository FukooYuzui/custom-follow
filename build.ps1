$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    python -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)"
    if ($LASTEXITCODE -eq 0) {
        python -m venv .venv
    } else {
        py -3.12 -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required for rebuilding.' }
}
& $taskPython -m pip install -r requirements.txt pyinstaller==6.22.3
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $taskPython -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
if (Get-Command npm.cmd -ErrorAction SilentlyContinue) {
    npm.cmd ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'UI test dependencies failed.' }
    npm.cmd test
    if ($LASTEXITCODE -ne 0) { throw 'UI tests failed.' }
}
& $taskPython -m PyInstaller --noconfirm --clean --windowed --onedir --name CustomFollow --icon assets/app.ico --add-data 'assets;assets' --collect-all webview --hidden-import pystray._win32 --exclude-module tkinter --distpath build/release --workpath build/work main.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot 'CustomFollow') | Out-Null
Copy-Item -LiteralPath 'build\release\CustomFollow\CustomFollow.exe' -Destination 'CustomFollow\CustomFollow.exe' -Force
Copy-Item -LiteralPath 'build\release\CustomFollow\_internal' -Destination 'CustomFollow' -Recurse -Force
