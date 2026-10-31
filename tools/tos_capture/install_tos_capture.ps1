# Installs the /ES and /NQ capture on this PC. The operator runs it once, by hand:
#
#     powershell -ExecutionPolicy Bypass -File tools\tos_capture\install_tos_capture.ps1
#
# It copies the capture to %LOCALAPPDATA%\TosCapture, so a branch change in a checkout
# cannot alter a running feed, and registers a scheduled task that starts it at logon.
# Run it again after the script changes. The task runs as whoever is logged on, only
# while they are, so ThinkorSwim and Excel share a desktop; it holds no credential.
#
# ASCII only: Windows PowerShell 5.1 reads a file with no byte order mark as ANSI.
$ErrorActionPreference = 'Stop'

$TaskName = 'ToS 0DTE Capture'
$RestartCount = 3
$RestartMinutes = 5

$target = Join-Path $env:LOCALAPPDATA 'TosCapture'
if (-not (Test-Path -LiteralPath $target)) {
    [void](New-Item -ItemType Directory -Path $target -Force)
}
$script = Join-Path $target 'tos_capture.ps1'
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'tos_capture.ps1') -Destination $script -Force

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $script + '"')
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
    -RestartCount $RestartCount -RestartInterval (New-TimeSpan -Minutes $RestartMinutes) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -Hidden
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

[void](Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Force)

Write-Output ('Registered the task "' + $TaskName + '". It starts at the next logon.')
Write-Output ('To start it now: Start-ScheduledTask -TaskName "' + $TaskName + '"')
Write-Output ('Its log: ' + (Join-Path $target 'capture.log'))
