#Requires -Version 5.1
<#
.SYNOPSIS
    Uninstaller for the Shuffles context-menu tool.

.DESCRIPTION
    Removes the registry entries created by installer.ps1.
    Does NOT delete script files.
#>

Add-Type -AssemblyName System.Windows.Forms

$RegBase     = "HKCU:\Software\Classes\Directory\Background\shell"
$KeyOrganize = "$RegBase\ShufflesOrganize"
$VbsFile     = Join-Path $PSScriptRoot "hide_and_run.vbs"

function Show-Dialog {
    param([string]$Title, [string]$Message, [string]$Buttons = "OK",
          [string]$Icon = "Information")
    return [System.Windows.Forms.MessageBox]::Show(
        $Message, $Title,
        [System.Windows.Forms.MessageBoxButtons]::$Buttons,
        [System.Windows.Forms.MessageBoxIcon]::$Icon
    )
}

# ── Confirm ───────────────────────────────────────────────────────────────────

$confirm = Show-Dialog -Title "Shuffles Uninstaller" `
    -Message "This will remove the Shuffles context menu entries from your registry.`n`nThe script files in:`n  $PSScriptRoot`nwill NOT be deleted.`n`nProceed?" `
    -Buttons "YesNo" -Icon "Warning"

if ($confirm -ne "Yes") {
    Write-Host "Uninstall cancelled."
    exit 0
}

# ── Remove registry entries ───────────────────────────────────────────────────

$removed = 0
foreach ($key in @($KeyOrganize)) {
    if (Test-Path $key) {
        try {
            Remove-Item -Path $key -Recurse -Force -ErrorAction Stop
            Write-Host "Removed registry key: $key"
            $removed++
        } catch {
            Show-Dialog -Title "Shuffles Uninstaller - Error" `
                -Message "Failed to remove registry key:`n  $key`n`nError: $_" `
                -Icon "Error"
        }
    } else {
        Write-Host "Key not found (already removed?): $key"
    }
}

# ── Remove generated helper files ─────────────────────────────────────────────

if (Test-Path $VbsFile) {
    try {
        Remove-Item $VbsFile -Force
        Write-Host "Removed: $VbsFile"
    } catch {
        Write-Host "WARNING: Could not remove $VbsFile - $_"
    }
}

$RegFile = Join-Path $PSScriptRoot "setup\setup.reg"
if (Test-Path $RegFile) {
    try {
        Remove-Item $RegFile -Force
        Write-Host "Removed: $RegFile"
    } catch {
        Write-Host "WARNING: Could not remove $RegFile - $_"
    }
}

# ── Done ──────────────────────────────────────────────────────────────────────

Show-Dialog -Title "Shuffles Uninstalled" `
    -Message "Registry entries removed ($removed key(s) deleted).`n`nSign out and back in (or restart Explorer) to see the context menu entries disappear."