param([ValidateSet('simulation','laptop')][string]$Mode = 'simulation')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$projectPython = Join-Path (Get-Location) '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    Write-Host 'Creating a Python 3.12 environment. Install Python 3.12 first if this fails.'
    py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required to create this environment.' }
}
& $projectPython -m pip install -r requirements-laptop.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
if ($Mode -eq 'simulation') {
    & $projectPython -m attendance --config config/simulation.toml init --demo
} else {
    & $projectPython -m attendance --config config/laptop.toml setup-face-models
    if ($LASTEXITCODE -ne 0) { throw 'Face model installation failed.' }
    & $projectPython -m attendance --config config/laptop.toml init
}
if ($LASTEXITCODE -ne 0) { throw 'Initialization failed.' }
Write-Host 'Open http://127.0.0.1:8765 in your browser. Press Ctrl+C here to stop.'
& $projectPython -m attendance --config "config/$Mode.toml" serve
