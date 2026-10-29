param([string]$FrontendRoot = (Join-Path $PSScriptRoot '../../Researchpilot-FE'))
$ErrorActionPreference = 'Stop'
$connectorRepo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$frontendPath = (Resolve-Path $FrontendRoot).Path
Push-Location $connectorRepo
try {
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $connectorRepo '.local-tools/pyinstaller-cache-full'
    & .venv/Scripts/python.exe -m PyInstaller --noconfirm --onefile --windowed --name 'ResearchPilot Connector' --distpath .local-tools/connector-dist --workpath .local-tools/connector-build-with-tk --specpath .local-tools connector/connector.py
    if ($LASTEXITCODE -ne 0) { throw 'Connector executable build failed.' }
    $downloadFolder = Join-Path $frontendPath 'public/downloads'
    New-Item -ItemType Directory -Path $downloadFolder -Force | Out-Null
    $archive = Join-Path $downloadFolder 'ResearchPilot-Connector.zip'
    Compress-Archive -LiteralPath @((Join-Path $connectorRepo '.local-tools/connector-dist/ResearchPilot Connector.exe'), (Join-Path $PSScriptRoot 'README.txt')) -DestinationPath $archive -Force
    if ((Get-Item -LiteralPath $archive).Length -ge 25MB) { throw 'Connector ZIP exceeds the Cloudflare static asset size limit.' }
    $hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath (Join-Path $downloadFolder 'ResearchPilot-Connector.sha256') -Value "$hash  ResearchPilot-Connector.zip" -Encoding ascii
    Write-Output "Windows connector download prepared at $archive"
} finally {
    Pop-Location
}
