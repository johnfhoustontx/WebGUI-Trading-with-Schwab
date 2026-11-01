# A by-hand check of the watchdog and of the identity check on a stop.
# Run by hand on the PC: powershell -NoProfile -File check_watchdog.ps1 -WorkFolder <dir>
#
# It starts no Excel. A small PowerShell stands in for the copy, carrying /automation in
# its command line, and this thread blocks in a wait as a stuck COM call would block it.
#
# What this does NOT show: Excel itself hanging, and a real stuck COM call throwing once
# its server has gone. Neither can be produced honestly here. The second is the documented
# behaviour of COM (an RPC failure), and Test-ExcelAlive's restart path has been run live
# by killing the copy (check_live.ps1). The second stage, for a call that does not throw,
# runs in a child PowerShell at the end, because it ends the process it runs in.
param([Parameter(Mandatory = $true)][string]$WorkFolder)
. (Join-Path $PSScriptRoot '..\tos_capture.ps1') -Once
$ErrorActionPreference = 'Continue'
[void][IO.Directory]::CreateDirectory($WorkFolder)
$WorkDir = $WorkFolder
$LogPath = Join-Path $WorkFolder 'check_watchdog.log'
$ExcelIdFile = Join-Path $WorkFolder 'check_watchdog.excel_id'
$script:Watch.LogPath = $LogPath
if (Test-Path -LiteralPath $LogPath) { Remove-Item -LiteralPath $LogPath }

$fails = 0
function Check([string]$Name, $Got, $Want) {
    if ("$Got" -ceq "$Want") { "ok   $Name [$Got]" } else { "FAIL $Name : got [$Got] want [$Want]"; $script:fails++ }
}
function Start-StandIn {
    $text = "Start-Sleep -Seconds 180; '/automation'"
    return Start-Process -FilePath 'powershell.exe' -ArgumentList ('-NoProfile -Command "' + $text + '"') -WindowStyle Hidden -PassThru
}

$standIn = Start-StandIn
Start-Sleep -Seconds 1
try {
    $owned = New-OwnedRecord $standIn 'powershell' '/automation'

    # Anything that does not match is left alone
    $later = [pscustomobject]@{ Process = $null; Id = $owned.Id; StartTime = $owned.StartTime.AddSeconds(1); Name = 'powershell'; Switch = '/automation' }
    Check 'another start time is left alone' ((Stop-OwnedExcel $later) -match 'left alone') $true
    $named = [pscustomobject]@{ Process = $null; Id = $owned.Id; StartTime = $owned.StartTime; Name = 'EXCEL'; Switch = '/automation' }
    Check 'another name is left alone' ((Stop-OwnedExcel $named) -match 'left alone') $true
    $switched = [pscustomobject]@{ Process = $null; Id = $owned.Id; StartTime = $owned.StartTime; Name = 'powershell'; Switch = '/restore' }
    Check 'another command line is left alone' ((Stop-OwnedExcel $switched) -match 'left alone') $true
    Check 'and it is still running' $standIn.HasExited $false

    # A leftover from a previous run, by what it recorded
    Write-ExcelIdFile ([pscustomobject]@{ Id = $owned.Id; StartTime = $owned.StartTime })
    Stop-LeftoverExcel
    Check 'a recorded id that is not Excel is left alone' $standIn.HasExited $false
    Check 'and the record is cleared' (Test-Path -LiteralPath $ExcelIdFile) $false
    [IO.File]::WriteAllText($ExcelIdFile, [string]$owned.Id, $Utf8NoBom)
    Stop-LeftoverExcel
    Check 'a bare id says nothing about whose it is' $standIn.HasExited $false

    # The watchdog, with short limits
    $HangLimitSec = 8
    $HangExitAfterSec = 3600     # the second stage has a process of its own, below
    $SshTimeoutSec = 1
    $SshHangMarginSec = 1
    $WatchdogPollSec = 1
    $script:Watch.Owned = $owned
    Start-Watchdog
    Check 'the limit is the one asked for' $script:Watch.LimitSec 8

    # A healthy loop beating through a wait longer than the limit
    Wait-Sleeping 12
    Check 'a beating loop is not touched' $standIn.HasExited $false

    # This thread stuck: no heartbeat, and nothing else running here
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $stopped = $standIn.WaitForExit(30000)
    $took = $clock.Elapsed.TotalSeconds
    Check 'the stand-in is stopped' $stopped $true
    Check 'not before the limit' ($took -ge 7) $true
    Check 'and soon after it' ($took -lt 14) $true
    "     stopped after $([math]::Round($took, 1)) seconds"
    Start-Sleep -Seconds 1
    $line = @([IO.File]::ReadAllLines($LogPath) | Where-Object { $_ -match 'watchdog: no heartbeat' })
    Check 'it logs why, once' $line.Length 1
    if ($line.Length -gt 0) { '     ' + $line[0] }

    # It does not keep firing at nothing while the restart happens
    Start-Sleep -Seconds 4
    $line = @([IO.File]::ReadAllLines($LogPath) | Where-Object { $_ -match 'watchdog: no heartbeat' })
    Check 'it waits a whole limit before firing again' $line.Length 1

    # It ends when told, and its thread goes
    $clock = [Diagnostics.Stopwatch]::StartNew()
    Stop-Watchdog
    Check 'it stops within a few seconds' ($clock.Elapsed.TotalSeconds -lt 5) $true
    Check 'and is gone' ($null -eq $script:WatchShell) $true
} finally {
    Stop-Watchdog
    if (-not $standIn.HasExited) { Stop-Process -Id $standIn.Id -Force }
}

# The second stage: a loop that never comes back ends the process. A child, because of that.
$childLog = Join-Path $WorkFolder 'check_watchdog_child.log'
if (Test-Path -LiteralPath $childLog) { Remove-Item -LiteralPath $childLog }
$childScript = Join-Path $WorkFolder 'check_watchdog_child.ps1'
$capture = Join-Path $PSScriptRoot '..\tos_capture.ps1'
$childLines = @(
    ('. ''' + $capture + ''' -Once'),
    ('$LogPath = ''' + $childLog + ''''),
    '$script:Watch.LogPath = $LogPath',
    '$HangLimitSec = 5',
    '$HangExitAfterSec = 4',
    '$SshTimeoutSec = 1',
    '$SshHangMarginSec = 1',
    '$WatchdogPollSec = 1',
    'Start-Watchdog',
    # Stuck, as a COM call that does not throw is: no heartbeat from here on.
    'Start-Sleep -Seconds 120',
    '[IO.File]::AppendAllText($LogPath, ''the loop came back'')'
)
[IO.File]::WriteAllLines($childScript, $childLines)
$child = Start-Process -FilePath 'powershell.exe' -ArgumentList ('-NoProfile -ExecutionPolicy Bypass -File "' + $childScript + '"') -WindowStyle Hidden -PassThru
$clock = [Diagnostics.Stopwatch]::StartNew()
$ended = $child.WaitForExit(40000)
$took = $clock.Elapsed.TotalSeconds
Check 'a loop that never comes back ends the process' $ended $true
if ($ended) {
    Check 'not before the stop plus the wait' ($took -ge 8) $true
    Check 'and soon after' ($took -lt 20) $true
    "     ended after $([math]::Round($took, 1)) seconds"
    $lines = @([IO.File]::ReadAllLines($childLog))
    Check 'the first stage logged' (@($lines | Where-Object { $_ -match 'watchdog: no heartbeat' }).Length) 1
    Check 'the second stage logged why' (@($lines | Where-Object { $_ -match 'ending this process' }).Length) 1
    Check 'the loop never came back' (@($lines | Where-Object { $_ -match 'came back' }).Length) 0
} else {
    Stop-Process -Id $child.Id -Force
}

# A loop that does come back after the stop is left alone.
$childLog = Join-Path $WorkFolder 'check_watchdog_child2.log'
if (Test-Path -LiteralPath $childLog) { Remove-Item -LiteralPath $childLog }
$childLines[1] = '$LogPath = ''' + $childLog + ''''
$childLines[9] = 'Start-Sleep -Seconds 8; Update-Heartbeat; Wait-Sleeping 12'
$childLines[10] = '[IO.File]::AppendAllText($LogPath, ''the loop came back'')'
[IO.File]::WriteAllLines($childScript, $childLines)
$child = Start-Process -FilePath 'powershell.exe' -ArgumentList ('-NoProfile -ExecutionPolicy Bypass -File "' + $childScript + '"') -WindowStyle Hidden -PassThru
[void]$child.WaitForExit(40000)
$lines = @([IO.File]::ReadAllLines($childLog))
Check 'a loop that came back is not ended' (@($lines | Where-Object { $_ -match 'ending this process' }).Length) 0
Check 'and it carried on' (@($lines | Where-Object { $_ -match 'came back' }).Length) 1
if (-not $child.HasExited) { Stop-Process -Id $child.Id -Force }
"failures: $fails"
