[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$output = Join-Path $repo 'dist'
New-Item -ItemType Directory -Path $output -Force | Out-Null
$stage = Join-Path $output ('companion-stage-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path (Join-Path $stage 'backend') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $stage 'companion') -Force | Out-Null
foreach ($file in @('service.py','desktop.py','database.py','webapp.py','README.md')) {
    Copy-Item -LiteralPath (Join-Path $repo ('backend\' + $file)) -Destination (Join-Path $stage 'backend')
}
Copy-Item -LiteralPath (Join-Path $repo 'backend\static') -Destination (Join-Path $stage 'backend') -Recurse
foreach ($file in @('Setup-Companion.ps1','Start-Companion.ps1','Setup-Companion.cmd','Start-Companion.cmd')) {
    Copy-Item -LiteralPath (Join-Path $repo ('companion\' + $file)) -Destination (Join-Path $stage 'companion')
}
Copy-Item -LiteralPath (Join-Path $repo 'LICENSE') -Destination $stage
New-Item -ItemType Directory -Path (Join-Path $stage 'docs') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $repo 'docs\CLOUD-CENSUS.md') -Destination (Join-Path $stage 'docs')
Copy-Item -LiteralPath (Join-Path $repo 'docs\BLIZZARD-POLICY.md') -Destination (Join-Path $stage 'docs')
$zip = Join-Path $output 'OlympusCensusCompanion-windows.zip'
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -Force
(Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash | Set-Content -LiteralPath ($zip + '.sha256')
Write-Host $zip
# Keep the named staging folder for review; it contains only the explicit runtime allowlist.
