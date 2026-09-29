# Puts a "White Build Price Check" shortcut on your Desktop that opens the control panel.
# Run from the repo folder:  powershell -ExecutionPolicy Bypass -File create_shortcut.ps1

$ErrorActionPreference = "Stop"

$Here    = Split-Path -Parent $MyInvocation.MyCommand.Path
$Pythonw = Join-Path $Here ".venv\Scripts\pythonw.exe"
$Panel   = Join-Path $Here "control_panel.pyw"

if (-not (Test-Path $Pythonw)) { throw "No .venv found in $Here - do the setup steps in README.md first." }

# Works for normal and OneDrive-redirected Desktops.
$Desktop = [Environment]::GetFolderPath("Desktop")
$Link    = Join-Path $Desktop "White Build Price Check.lnk"

$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($Link)
$sc.TargetPath       = $Pythonw
$sc.Arguments        = "`"$Panel`""
$sc.WorkingDirectory = $Here
$sc.IconLocation     = "$env:SystemRoot\System32\imageres.dll,109"
$sc.Description      = "Run and test the White Build price check"
$sc.Save()

Write-Host "Shortcut created: $Link"
