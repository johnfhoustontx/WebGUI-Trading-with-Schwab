# A by-hand check of the capture's flows against the real ThinkorSwim, with a fake ssh
# that only records what it is sent, so nothing reaches the server.
# Run by hand on the PC with ThinkorSwim already up, never during a session you rely on:
#   powershell -NoProfile -File check_live.ps1 -WorkFolder <dir> -PlanFile <plan.json>
#
# It starts a private hidden Excel of its own, and stops it by process id. It leaves any
# other Excel alone. It is read-only towards ThinkorSwim: it never starts it and never
# types into it. -CheckHandOff also opens one spare workbook the way a double-click does,
# in a separate visible Excel, and refuses if the operator has an Excel open.
param(
    [Parameter(Mandatory = $true)][string]$WorkFolder,
    [Parameter(Mandatory = $true)][string]$PlanFile,
    [string]$PythonExe = 'python',
    [switch]$CheckHandOff
)
$books = Join-Path $WorkFolder 'books'
. (Join-Path $PSScriptRoot '..\tos_capture.ps1') -PlanFile $PlanFile -Once -WorkbookDir $books
$ErrorActionPreference = 'Stop'
[void][IO.Directory]::CreateDirectory($WorkFolder)
$WorkDir = $WorkFolder
$LogPath = Join-Path $WorkFolder 'check_live.log'
$ExcelIdFile = Join-Path $WorkFolder 'check_live.excel_id'
$script:Watch.LogPath = $LogPath
$SshExe = $PythonExe
$SshArguments = '"' + (Join-Path $PSScriptRoot 'fake_ssh.py') + '"'
if (Test-Path -LiteralPath $books) { Remove-Item -LiteralPath $books -Recurse -Force }

$fails = 0
function Check([string]$Name, $Got, $Want) {
    if ("$Got" -ceq "$Want") { "ok   $Name [$Got]" } else { "FAIL $Name : got [$Got] want [$Want]"; $script:fails++ }
}
function Get-OtherExcel {
    # Every Excel that is not a private copy started with /automation.
    return @(Get-CimInstance Win32_Process -Filter "Name='EXCEL.EXE'" | Where-Object { $_.CommandLine -notmatch '/automation' })
}
function Get-ExcelOption {
    # Every value Excel keeps under its Options key, as text, so two reads compare.
    $lines = New-Object System.Collections.ArrayList
    foreach ($version in @(Get-ChildItem 'HKCU:\Software\Microsoft\Office' -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -match '^\d+\.\d+$' })) {
        $key = Join-Path $version.PSPath 'Excel\Options'
        if (-not (Test-Path -LiteralPath $key)) { continue }
        $values = Get-ItemProperty -LiteralPath $key
        foreach ($item in @($values.PSObject.Properties | Where-Object { $_.Name -notmatch '^PS' } | Sort-Object Name)) {
            [void]$lines.Add($version.PSChildName + ' ' + $item.Name + ' = ' + $item.Value)
        }
    }
    return , @($lines.ToArray())
}

$optionsBefore = Get-ExcelOption
"Excel options before the run:"
$optionsBefore | ForEach-Object { '     ' + $_ }

try {
    # The boundary
    $script:Plan = [pscustomobject]@{ interval_sec = 5 }
    Wait-NextBoundary
    $now = Get-EpochSeconds
    Check 'woke on a boundary' ([int]($now % 5) -eq 0 -and ($now % 5) -lt 1) $true
    $script:Plan = $null

    # A normal capture, with the send going to the fake
    Invoke-Prepare
    $firstExcel = $script:Owned.Id
    Check 'both ready' $script:Ladders.Count 2
    Check 'the private copy ignores other programs' $script:Excel.IgnoreRemoteRequests $true
    foreach ($root in @($script:Ladders.Keys)) { "     $root settled: $($script:Ladders[$root].Settled), rows: $($script:Ladders[$root].Rows.Count)" }

    # An unsettled ladder is not read on the boundary it was written
    $root = @($script:Ladders.Keys)[0]
    $keep = $script:Ladders[$root].Settled
    $script:Ladders[$root].Settled = $false
    $script:Ladders[$root].BuiltSlot = [math]::Floor((Get-EpochSeconds) / [double]$script:Plan.interval_sec)
    Check 'unsettled on its own boundary: nothing read' ($null -eq (Read-Snapshot $script:Ladders[$root] $script:Plan)) $true
    Check 'the ladder is kept' $script:Ladders.ContainsKey($root) $true
    Check 'and the reason goes in the status' ($script:Problems[$root] -match 'still filling') $true
    [void]$script:Problems.Remove($root)
    $script:Ladders[$root].Settled = $keep
    $script:Ladders[$root].BuiltSlot = -1.0

    $sent = Invoke-Capture
    Check 'nothing leaks from Invoke-Capture' ($null -eq $sent) $true
    Check 'two snapshots' $script:SnapshotCount 2
    foreach ($root in @($script:Ladders.Keys)) { "     $root read: $($script:Ladders[$root].MostRead)" }
    $path = Join-Path $books ([string]$script:Plan.expiry + '.xlsx')
    Check 'workbook written' (Test-Path $path) $true

    if ($CheckHandOff) {
        # The operator double-clicking a workbook while the capture runs.
        $others = @(Get-OtherExcel)
        if ($others.Length -gt 0) {
            "hand-off NOT MEASURED: an Excel not started by this check is open (" + ($others.ProcessId -join ', ') + ')'
        } else {
            $spare = Join-Path $WorkFolder 'spare.xlsx'
            Copy-Item -LiteralPath $path -Destination $spare -Force
            $count = $script:Excel.Workbooks.Count
            Start-Process -FilePath $spare
            $opened = @()
            for ($wait = 0; $wait -lt 40 -and $opened.Length -eq 0; $wait++) {
                Start-Sleep -Milliseconds 500
                $opened = @(Get-OtherExcel)
            }
            Check 'it opened in a separate process' $opened.Length 1
            if ($opened.Length -eq 1) {
                $id = $opened[0].ProcessId
                "     process $id, command line: " + $opened[0].CommandLine
                Check 'not the private copy' ($id -ne $script:Owned.Id) $true
                Check '/automation absent' ($opened[0].CommandLine -match '/automation') $false
                Start-Sleep -Seconds 3
                Check 'the private copy still holds what it held' $script:Excel.Workbooks.Count $count
                $window = (Get-Process -Id $id).MainWindowTitle
                "     its window: [$window]"
                Stop-Process -Id $id -Force
                Start-Sleep -Milliseconds 500
            }
            Remove-Item -LiteralPath $spare -Force
        }
    }

    # A second capture appends to the same file
    Invoke-Prepare -KeepTime
    Invoke-Capture
    Check 'still two' $script:SnapshotCount 2

    # A workbook somebody is holding: skipped, the capture still counted
    $hold = [IO.File]::Open($path, 'Open', 'ReadWrite', 'None')
    $sizeBefore = $hold.Length
    Invoke-Capture
    Check 'capture goes on while the workbook is held' $script:SnapshotCount 2
    Check 'held workbook untouched' $hold.Length $sizeBefore
    $hold.Close()

    # A dropped instrument does not stop the other, and is left to be rebuilt
    $script:Ladders['NQ'].Sheet.Cells.ClearContents() | Out-Null
    Invoke-Capture
    Check 'the other instrument still goes' $script:SnapshotCount 1
    Check 'NQ is left to be rebuilt' $script:Ladders.ContainsKey('NQ') $false
    Invoke-Prepare
    Check 'the next prepare rebuilds it' $script:Ladders.ContainsKey('NQ') $true

    # Excel dies: seen, restarted, and a new copy is recorded
    Stop-Process -Id $script:Owned.Id -Force
    Start-Sleep -Seconds 1
    Check 'death seen' (Test-ExcelAlive) $false
    Invoke-Capture
    Check 'a read on a dead Excel sends nothing and raises nothing' $script:SnapshotCount 0
    Invoke-Prepare -KeepTime
    Check 'restarted' ($script:Owned.Id -ne $firstExcel) $true
    Check 'ready again' $script:Ladders.Count 2
    Invoke-Capture
    Check 'and captures' $script:SnapshotCount 2
} finally {
    $excelId = if ($null -ne $script:Owned) { $script:Owned.Id } else { 0 }
    Stop-CaptureExcel
    Start-Sleep -Milliseconds 500
    Check 'stopped by id' ($null -eq (Get-Process -Id $excelId -ErrorAction SilentlyContinue)) $true
}

# ThinkorSwim not there: the look-up is shadowed in this process only, nothing touches it
$real = Get-Command Get-Process -CommandType Cmdlet
function Get-Process {
    param([string]$Name, [int]$Id, $ErrorAction)
    if ($Name -eq 'thinkorswim') { return }
    & $real -Id $Id -ErrorAction SilentlyContinue
}
try {
    $script:Plan = $null
    Invoke-Prepare
    Check 'with no ThinkorSwim it says so' ($script:GeneralProblem -match 'ThinkorSwim') $true
    Invoke-Capture
    Check 'and sends no snapshot' $script:SnapshotCount 0
} finally {
    Stop-CaptureExcel
    Remove-Item Function:\Get-Process
}

$optionsAfter = Get-ExcelOption
"Excel options after the run:"
$optionsAfter | ForEach-Object { '     ' + $_ }
Check 'no Excel option moved' ($optionsAfter -join '|') ($optionsBefore -join '|')
$restored = @(Get-CimInstance Win32_Process -Filter "Name='EXCEL.EXE'" | Where-Object { $_.CommandLine -match '/restore' })
Check 'no Excel was brought back by Windows' $restored.Length 0
"failures: $fails"
