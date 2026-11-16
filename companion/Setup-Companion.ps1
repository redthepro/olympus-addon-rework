[CmdletBinding()]
param(
    [string]$ClientId,
    [Security.SecureString]$ClientSecret,
    [string]$Region,
    [string]$Namespace,
    [string]$Realm,
    [string]$Guild,
    [switch]$CredentialsOnly,
    [string]$StateDirectory = (Join-Path $env:LOCALAPPDATA 'OlympusCensus')
)
$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne 'Win32NT') { throw 'This setup uses Windows credential protection.' }
Write-Host 'Olympus API census: local companion setup'
Write-Host 'Create your own application at https://develop.battle.net/access/clients .'
Write-Host 'Public roster access uses application credentials; no player login or RDS is needed.'
$path = Join-Path $StateDirectory 'settings.json'
$existing = $null
if (Test-Path -LiteralPath $path) {
    $existing = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    if ($existing.version -ne 1) { throw 'Unsupported existing settings version.' }
}
$targetParameters = @('Region','Namespace','Realm','Guild') | Where-Object { $PSBoundParameters.ContainsKey($_) }
$keepTargets = [bool]$CredentialsOnly
if ($CredentialsOnly -and (-not $existing -or $targetParameters.Count -gt 0)) {
    throw '-CredentialsOnly requires existing settings and cannot be combined with target parameters.'
}
if ($existing -and $targetParameters.Count -eq 0 -and -not $CredentialsOnly) {
    $keep = Read-Host 'Keep the existing guild targets and polling settings? (Enter for yes; no to change targets)'
    if ([string]::IsNullOrWhiteSpace($keep) -or $keep.Trim().ToLowerInvariant() -eq 'yes') { $keepTargets = $true }
    elseif ($keep.Trim().ToLowerInvariant() -ne 'no') { throw 'Enter yes or no. Existing settings were not changed.' }
}
if (-not $ClientId) { $ClientId = Read-Host 'Battle.net client ID' }
if (-not $ClientSecret) { $ClientSecret = Read-Host 'Battle.net client secret (hidden)' -AsSecureString }
if (-not $keepTargets) {
    if (-not $Region) { $Region = Read-Host 'Region (us, eu, kr, tw; Enter for us)' }
    if ([string]::IsNullOrWhiteSpace($Region)) { $Region = 'us' }
    $Region = $Region.Trim().ToLowerInvariant()
    if ($Namespace) { $Namespace = $Namespace.Trim().ToLowerInvariant() }
    if ($Realm) { $Realm = $Realm.Trim().ToLowerInvariant() }
    if (-not $Namespace) {
        $game = Read-Host 'Game version: forever or retail (Enter for forever)'
        if ($game) { $game = $game.Trim().ToLowerInvariant() }
        if (-not $game -or $game -eq 'forever') { $Namespace = 'profile-forever-' + $Region.ToLowerInvariant() }
        elseif ($game -eq 'retail') { $Namespace = 'profile-' + $Region.ToLowerInvariant() }
        else { throw 'For another game version, run setup with its verified -Namespace parameter.' }
    }
    if ($Namespace -eq ('profile-forever-' + $Region.ToLowerInvariant())) {
        if (-not $Realm) { $Realm = 'normal' }
        if ($Realm -notin @('normal','pvp','roleplay','roleplay-pvp','global')) { throw 'Forever scope must be normal, pvp, roleplay, roleplay-pvp or global, not a realm slug.' }
        Write-Host ('Forever scope: ' + $Realm + '. Normal requires matching guild API ruleset metadata. Layers are not separate populations.')
        Write-Host 'Forever API compatibility still requires a successful roster fetch.'
    } elseif (-not $Realm) { $Realm = Read-Host 'Realm slug from the API' }
    if (-not $Guild) { $Guild = Read-Host 'Guild slug from the API (Enter for olympus)' }
    if ([string]::IsNullOrWhiteSpace($Guild)) { $Guild = 'olympus' }
    $Region = $Region.Trim().ToLowerInvariant()
    $Namespace = $Namespace.Trim().ToLowerInvariant()
    $Realm = $Realm.Trim().ToLowerInvariant()
    $Guild = $Guild.Trim().ToLowerInvariant()
    if ($Realm -eq 'global' -and $Namespace -ne ('profile-forever-' + $Region)) { throw 'Global scope is reserved for the experimental Forever adapter.' }
    if ($Region -notin @('us','eu','kr','tw')) { throw 'Unsupported region.' }
    foreach ($value in @($Namespace, $Realm, $Guild)) {
        if ($value -notmatch '^[a-z0-9]+(-[a-z0-9]+)*$') { throw 'Use API slugs: lowercase letters, numbers, and single hyphens.' }
    }
    if (-not $Namespace.StartsWith('profile-') -or -not $Namespace.EndsWith('-' + $Region) -or
        $Namespace.Length -gt 64 -or $Realm.Length -gt 80 -or $Guild.Length -gt 96) { throw 'Invalid target namespace or slug length.' }
}
if ([string]::IsNullOrWhiteSpace($ClientId) -or $ClientId.Length -gt 256 -or $ClientSecret.Length -eq 0) {
    throw 'Client ID and secret are required.'
}
$directory = New-Item -ItemType Directory -Path $StateDirectory -Force
$acl = New-Object Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true, $false)
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().User
$rights = [Security.AccessControl.FileSystemRights]::FullControl
$inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
$propagate = [Security.AccessControl.PropagationFlags]::None
$allow = [Security.AccessControl.AccessControlType]::Allow
$acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($identity, $rights, $inherit, $propagate, $allow)))
$acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule('SYSTEM', $rights, $inherit, $propagate, $allow)))
Set-Acl -LiteralPath $directory.FullName -AclObject $acl
$config = if ($existing) { $existing } else { [pscustomobject][ordered]@{
    version = 1
    clientId = ''
    protectedSecret = ''
    targets = @()
    intervalSeconds = 900
    port = 8787
} }
$config.clientId = $ClientId.Trim()
$config.protectedSecret = ConvertFrom-SecureString $ClientSecret
# Ensure a single target remains a nested JSON array in both PowerShell 5.1 and 7.
if (-not $keepTargets) { $config.targets = ,@($Region, $Namespace, $Realm, $Guild) }
$path = Join-Path $directory.FullName 'settings.json'
$temporary = Join-Path $directory.FullName ('settings-' + [guid]::NewGuid().ToString('N') + '.tmp')
try {
    $config | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $path -Force
} finally {
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
}
Write-Host ('Settings saved to ' + $path)
Write-Host 'The secret is encrypted for this Windows account. Run Start-Companion.cmd next.'
