[CmdletBinding()]
param(
    [string]$StateDirectory = (Join-Path $env:LOCALAPPDATA 'OlympusCensus'),
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
$path = Join-Path $StateDirectory 'settings.json'
if (-not (Test-Path -LiteralPath $path)) { throw 'Run Setup-Companion.cmd first.' }
$config = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
if ($config.version -ne 1) { throw 'Unsupported settings version. Run setup again.' }
$python = (Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$entry = Join-Path (Split-Path $PSScriptRoot -Parent) 'backend\desktop.py'
$variables = @('BNET_CLIENT_ID','BNET_CLIENT_SECRET','CENSUS_TARGETS','CENSUS_DB','CENSUS_PORT','CENSUS_INTERVAL')
$saved = @{}
foreach ($name in $variables) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$port = 0
if (-not [int]::TryParse([string]$config.port, [ref]$port) -or $port -lt 1024 -or $port -gt 65535) { throw 'Invalid local port.' }
Write-Host ('Olympus local companion: http://127.0.0.1:' + $port)
Write-Host 'Keep this window open. Press Ctrl+C to stop. No inbound port forwarding is needed.'
# Python opens the browser after binding the listener. A failed or duplicate
# launch cannot send the user to a dead page or an unrelated local service.
$arguments = @('-u', $entry)
if ($NoBrowser) { $arguments += '--no-browser' }
$pointer = [IntPtr]::Zero
try {
    $secret = ConvertTo-SecureString $config.protectedSecret
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    $env:BNET_CLIENT_ID = $config.clientId
    $env:BNET_CLIENT_SECRET = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    $env:CENSUS_TARGETS = ConvertTo-Json -InputObject $config.targets -Depth 5 -Compress
    $env:CENSUS_DB = Join-Path $StateDirectory 'census.sqlite3'
    $env:CENSUS_PORT = [string]$config.port
    $env:CENSUS_INTERVAL = [string]$config.intervalSeconds
    & $python @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Companion stopped with an error. Check the message above.' }
} finally {
    if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    foreach ($name in $variables) { [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process') }
}
