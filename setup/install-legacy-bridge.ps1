#Requires -Version 5.1
# Preserve compatibility for a known obsolete launcher without moving the source.
param([Parameter(Mandatory=$true)][string]$LegacyLauncher)
$ErrorActionPreference = 'Stop'
$LegacyLauncher = [IO.Path]::GetFullPath($LegacyLauncher)
if ([IO.Path]::GetFileName($LegacyLauncher) -ne 'hide_and_run.vbs') {
    throw 'The compatibility destination must be named hide_and_run.vbs.'
}
$Template = Join-Path $PSScriptRoot 'legacy-forwarder.vbs'
$Content = [IO.File]::ReadAllText($Template)
if (Test-Path -LiteralPath $LegacyLauncher) {
    $Existing = [IO.File]::ReadAllText($LegacyLauncher)
    if ($Existing -ne $Content) {
        throw 'Existing launcher differs; preserve and inspect it before replacing.'
    }
} else {
    New-Item -ItemType Directory -Path (Split-Path $LegacyLauncher -Parent) -Force | Out-Null
    [IO.File]::WriteAllText($LegacyLauncher, $Content, [Text.UTF8Encoding]::new($false))
}
Write-Host "Installed compatibility forwarder: $LegacyLauncher"
