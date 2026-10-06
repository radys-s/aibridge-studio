# Creates "AI Bridge Studio" shortcuts on the desktop and in the Start menu.
# The shortcut runs `uv run studio app` in this repository: Studio opens in its own window
# and stops when the window closes. Usage:  powershell -File scripts\install-shortcut.ps1
param([switch]$NoDesktop, [switch]$NoStartMenu)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$uv = (Get-Command uv -ErrorAction Stop).Source
$edge = @(
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

$targets = @()
if (-not $NoDesktop) { $targets += [Environment]::GetFolderPath("Desktop") }
if (-not $NoStartMenu) { $targets += Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs" }

$shell = New-Object -ComObject WScript.Shell
foreach ($dir in $targets) {
    $path = Join-Path $dir "AI Bridge Studio.lnk"
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $uv
    $lnk.Arguments = "run studio app"
    $lnk.WorkingDirectory = $repo
    $lnk.WindowStyle = 7  # minimized: the console only hosts the server
    $lnk.Description = "AI Bridge Studio"
    if ($edge) { $lnk.IconLocation = "$edge,0" }
    $lnk.Save()
    Write-Host "Created $path"
}
