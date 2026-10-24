#Requires -Version 5.1
<# Installs a per-user runtime in LocalAppData\Programs\Shuffles.
   The source checkout can be moved after installation. No administrator required. #>
param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'
if ($Uninstall) {
    & (Join-Path $PSScriptRoot 'uninstaller.ps1')
    exit
}
Add-Type -AssemblyName System.Windows.Forms
$InstallDir = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Programs\Shuffles'
$answer = [System.Windows.Forms.MessageBox]::Show(
    "Install/update Shuffles in:`n$InstallDir`n`nThe context menu will keep working if you move the source folder. Proceed?",
    'Shuffles Installer', 'YesNo', 'Question')
if ($answer -ne 'Yes') { exit 0 }
try {
    & (Join-Path $PSScriptRoot 'setup\repair-paths.ps1')
    [System.Windows.Forms.MessageBox]::Show(
        "Installed. Right-click empty space in a folder and choose Organize this folder.`n`nOn Windows 11, look under Show more options.",
        'Shuffles Installed', 'OK', 'Information') | Out-Null
} catch {
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'Shuffles Installer - Error', 'OK', 'Error') | Out-Null
    exit 1
}