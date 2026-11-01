# The /ES and /NQ 0-DTE chain capture, on the operator's Windows PC.
#
# ThinkorSwim serves these chains over RTD and RTD needs Excel, so this owns one
# private hidden Excel, reads the ladder out of it on the clock and sends each
# capture to vps2 over one ssh. The server builds the plan (the expiry, the series
# to try, the strike step and width, the interval), so nothing here names a date,
# a series or a cadence. Design: docs/plans/2026-10-31-futures-0dte-chain-feed-design.md
#
#   tos_capture.ps1                 runs until stopped (the scheduled task)
#   tos_capture.ps1 -Once           one capture now, whatever the session, then stop
#   -NoSend                         nothing goes over ssh except a plan fetch
#   -OutFile <path>                 also write what would be sent to this file
#   -PlanFile <path>                read the plan from this file, not the server
#   -WorkbookDir <path>             put the dated workbook here, not in Documents
#
# It only ever reads ThinkorSwim: it never starts it, never types into it, and
# never places or prepares an order. It never touches an Excel it did not start.
#
# ASCII only: Windows PowerShell 5.1 reads a file with no byte order mark as ANSI.
[CmdletBinding()]
param(
    [switch]$Once,
    [switch]$NoSend,
    [string]$OutFile,
    [string]$PlanFile,
    [string]$WorkbookDir
)

Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'

# -- the machine's own settings (everything else arrives in the plan) ------------

# One line, no inner quotes: PowerShell 5.1 drops quotes inside an ssh command line.
$SshExe = 'ssh'
$SshArguments = '-o BatchMode=yes -o ConnectTimeout=20 vps2 /home/administrator/dev/tools/futures_chain_remote.sh'
$SshDrainMs = 2000

$WorkDir = Join-Path $env:LOCALAPPDATA 'TosCapture'
$LogPath = Join-Path $WorkDir 'capture.log'
$LogKeepBytes = 20MB
$ExcelIdFile = Join-Path $WorkDir 'excel.pid'
$MutexName = 'Local\TosCapture0DTE'

# Excel is slow to register itself. Asking COM too early makes COM launch a second
# copy, and with no Excel open it failed outright (0x80080005).
$ExcelStartAttempts = 3
$ExcelIdleWaitMs = 30000
$ExcelAttachPauseSec = 2
$ExcelBusyAttempts = 10
$ExcelBusyPauseMs = 500
$XlCalculationAutomatic = -4105
$XlOpenXmlWorkbook = 51

$FillPollMs = 1000
$SleepSliceSec = 5
$WatchdogPollSec = 2
# Allowed above two whole ssh limits when working out the hang limit.
$SshHangMarginSec = 30

# -- the plan's settings ---------------------------------------------------------

# Each lives in config/futures_chain.toml [capture] and arrives in the plan. The PC
# and the server do not update together, so a plan from an older server may lack
# one: this default is then used, and it is logged once.
#
# A cell just written reads N/A or 0 until ThinkorSwim answers (about 730 contracts
# took about 3 seconds). Excel hands RTD updates over in batches, every 2 seconds
# by default, so a count of filled cells can sit still between two batches of a
# sheet that is still filling. A sheet is called filled only when the count has held
# for longer than two such batches, and at least one cell reads a value. A listed
# contract can stay N/A for good, so the wait is capped rather than waiting for all
# of them; a sheet that reaches the cap is not read on the boundary it was written.
$FillSettleSec = 5  # fill_settle_sec
$FillWaitMaxSec = 20  # fill_wait_max_sec
# Where to look for each candidate series: round strikes around the price. A listed
# series can read N/A at one strike and resolve at another, so several are tried.
$ProbeRoundSteps = 10  # probe_round_steps
$ProbeReach = 3  # probe_reach
# The ladder is rewritten around the price when the price has moved this share of
# the way to the ladder's end, because walls and the flip only see captured strikes.
$RecentreShare = 0.5  # recentre_share
# A hung ssh is killed after this: the next boundary's capture matters more.
$SshTimeoutSec = 60  # ssh_timeout_sec
# A failed plan fetch retries after the first, doubling up to the second. A burst of
# ssh connections has shut the operator out of the server before.
$NoPlanRetrySec = 60  # no_plan_retry_sec
$PlanRetryMaxSec = 900  # plan_retry_max_sec
# The floor against the contracts tried: below it the sheet has not filled, or
# ThinkorSwim is logged out. /NQ's uneven spacing lists 416 of 572 tried.
$MinReadFraction = 0.25  # min_read_fraction
# No heartbeat from the loop for this long and its Excel is stopped. It must exceed
# the longest honest stretch without a heartbeat, or a healthy Excel is killed. The
# loop beats through every sleep and every poll of a filling sheet, so that stretch
# is one call that cannot beat: one whole ssh (twice its limit, write then exit), one
# Excel start, or one workbook opened and saved (5 seconds at a session's size).
$HangLimitSec = 600  # hang_limit_sec
# Stopping the Excel should make the loop's stuck call throw. With still no heartbeat
# this long after the stop the call is not coming back, and a hung process has not
# failed, so nothing else would restart it: the capture ends itself, and the task's
# repeating trigger starts another. Long enough for the three tries at restarting Excel.
$HangExitAfterSec = 240  # hang_exit_after_sec
$PlanSettings = [ordered]@{
    fill_settle_sec = 'FillSettleSec'
    fill_wait_max_sec = 'FillWaitMaxSec'
    probe_round_steps = 'ProbeRoundSteps'
    probe_reach = 'ProbeReach'
    recentre_share = 'RecentreShare'
    ssh_timeout_sec = 'SshTimeoutSec'
    no_plan_retry_sec = 'NoPlanRetrySec'
    plan_retry_max_sec = 'PlanRetryMaxSec'
    min_read_fraction = 'MinReadFraction'
    hang_limit_sec = 'HangLimitSec'
    hang_exit_after_sec = 'HangExitAfterSec'
}

$RtdFields = @('MARK', 'BID', 'ASK', 'VOLUME', 'OPEN_INT', 'IMPL_VOL', 'DELTA')
$Invariant = [Globalization.CultureInfo]::InvariantCulture
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# -- state ---------------------------------------------------------------------

$script:Plan = $null
$script:Excel = $null
$script:Book = $null
$script:Sheets = @{}
$script:Owned = $null
$script:Ladders = @{}
$script:Problems = @{}
$script:PlanRetryAt = 0.0
$script:PlanRetryDelay = 0.0
$script:DefaultedSettings = @{}
$script:RestoreSeen = @{}
$script:WatchShell = $null
# Shared with the watchdog's thread.
$script:Watch = [hashtable]::Synchronized(@{
        Beat = 0.0; LimitSec = 0.0; ExitAfterSec = 0.0; Owned = $null; Stop = $false; LogPath = $LogPath
    })
$script:GeneralProblem = ''
$script:InSession = $false
$script:ThinkorswimId = $null
$script:SnapshotCount = 0
$script:ExitCode = 0

# -- small helpers -------------------------------------------------------------

function Get-EpochSeconds {
    # An instant, in no zone: Get-Date is the PC's local clock and the server
    # would read a Central wall clock as the wrong moment.
    return [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
}

function Write-Log {
    param([string]$Text)
    $line = [DateTimeOffset]::Now.ToString('yyyy-MM-dd HH:mm:ss zzz', $Invariant) + ' ' + $Text
    try {
        if (-not (Test-Path -LiteralPath $WorkDir)) {
            [void](New-Item -ItemType Directory -Path $WorkDir -Force)
        }
        if ((Test-Path -LiteralPath $LogPath) -and (Get-Item -LiteralPath $LogPath).Length -gt $LogKeepBytes) {
            Move-Item -LiteralPath $LogPath -Destination ($LogPath + '.1') -Force
        }
        [IO.File]::AppendAllText($LogPath, $line + "`r`n", $Utf8NoBom)
    } catch {
        # A log that cannot be written must never stop a capture.
        Write-Verbose ('log failed: ' + $_.Exception.Message)
    }
    Write-Verbose $line
}

function Resolve-UserPath {
    param([string]$Path)
    return $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
}

function ConvertTo-Reading {
    # A cell as a finite number, or $null: N/A, an empty cell, text that is not a
    # number and an Excel error (which Value2 hands back as an Int32) are all absent,
    # never zero.
    param($Cell)
    if ($null -eq $Cell) { return $null }
    if ($Cell -is [double]) {
        if ([double]::IsNaN($Cell) -or [double]::IsInfinity($Cell)) { return $null }
        return $Cell
    }
    if ($Cell -isnot [string]) { return $null }
    $parsed = 0.0
    $styles = [Globalization.NumberStyles]::Float -bor [Globalization.NumberStyles]::AllowThousands
    if ([double]::TryParse($Cell.Trim(), $styles, $Invariant, [ref]$parsed)) {
        if ([double]::IsNaN($parsed) -or [double]::IsInfinity($parsed)) { return $null }
        return $parsed
    }
    return $null
}

function ConvertTo-PercentReading {
    # IMPL_VOL as the percent it prints: "12.51%" is 12.51, which is what the wire
    # carries. The text must carry its percent sign. A bare number could be either
    # scale, and sending the wrong one misstates every gamma, so it is absent. The
    # value is never rescaled here.
    param($Cell)
    if ($Cell -isnot [string]) { return $null }
    $text = $Cell.Trim()
    if (-not $text.EndsWith('%')) { return $null }
    return ConvertTo-Reading $text.Substring(0, $text.Length - 1)
}

function Format-Strike {
    param([double]$Strike)
    return $Strike.ToString('0.####', $Invariant)
}

function Get-OptionSymbol {
    param([string]$Series, [string]$Side, [double]$Strike, [string]$Exchange)
    return './' + $Series + $Side + (Format-Strike $Strike) + ':' + $Exchange
}

function Get-RtdFormula {
    param([string]$Field, [string]$Symbol)
    return '=RTD("tos.rtd",,"' + $Field + '","' + $Symbol + '")'
}

function Test-StatesDate {
    # "2 NOV 26" is inside "12 NOV 26": the date must stand on its own.
    param([string]$Text, [string]$DateText)
    return $Text -match ('(^|\s)' + [regex]::Escape($DateText) + '(\s|$)')
}

function Get-DescribedFuture {
    # The future a description opens with: "/XXZ26:XCME 1/50 ...".
    param([string]$Text)
    if ($Text -match '^\s*(/[A-Za-z0-9]+:[A-Za-z]+)\s') { return $Matches[1].ToUpperInvariant() }
    return $null
}

function Get-DescribedSeries {
    # The series a description ends with: "... /XXX26:XCME 7600 CALL".
    param([string]$Text)
    if ($Text -match '\s/([A-Za-z0-9]+):[A-Za-z]+\s+[0-9.]+\s+(CALL|PUT)\s*$') {
        return $Matches[1].ToUpperInvariant()
    }
    return $null
}

function Release-Com {
    param($Object)
    if ($null -ne $Object) {
        try { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($Object) } catch {
            Write-Verbose ('release failed: ' + $_.Exception.Message)
        }
    }
}

# -- ssh -----------------------------------------------------------------------

function Invoke-Ssh {
    # One ssh, with Body (bytes) on its stdin. Returns what it did and never hangs.
    param([string]$Verb, [byte[]]$Body)
    $timeoutMs = [int]($SshTimeoutSec * 1000)

    # .NET Framework builds StandardInput's writer from the console's input encoding,
    # with no way to choose another, and on a UTF-8 console that writer puts a byte
    # order mark on the pipe the moment the process starts: the JSON would then
    # start with it. A console already on UTF-8 is told the same thing without the
    # mark. With no console the encoding is the ANSI page, which has no mark.
    try {
        if ([Console]::InputEncoding.GetPreamble().Length -gt 0) {
            [Console]::InputEncoding = $Utf8NoBom
        }
    } catch {
        Write-Verbose ('no console to set: ' + $_.Exception.Message)
    }

    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $SshExe
    $info.Arguments = $SshArguments + ' ' + $Verb
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = $Utf8NoBom
    $info.StandardErrorEncoding = $Utf8NoBom

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    [void]$process.Start()
    try {
        # Both drained at once: a child that fills one pipe while this waits on the
        # other would otherwise deadlock.
        $outTask = $process.StandardOutput.ReadToEndAsync()
        $errTask = $process.StandardError.ReadToEndAsync()

        # Bytes straight to the pipe, and the write itself bounded: an ssh that
        # stops reading would block a plain Write for ever.
        $wroteInTime = $true
        try {
            if ($null -ne $Body -and $Body.Length -gt 0) {
                $writeTask = $process.StandardInput.BaseStream.WriteAsync($Body, 0, $Body.Length)
                $wroteInTime = $writeTask.Wait($timeoutMs)
            }
            if ($wroteInTime) { $process.StandardInput.Close() }
        } catch {
            # ssh closed its end, usually because it failed: its exit says why.
            Write-Verbose ('stdin closed early: ' + $_.Exception.Message)
        }

        $exited = $wroteInTime -and $process.WaitForExit($timeoutMs)
        $timedOut = -not $exited
        if ($timedOut) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            [void]$process.WaitForExit($SshDrainMs)
        }
        $out = ''
        $err = ''
        if ($outTask.Wait($SshDrainMs)) { $out = $outTask.Result }
        if ($errTask.Wait($SshDrainMs)) { $err = $errTask.Result }
        $code = if ($timedOut) { -1 } else { $process.ExitCode }
        return [pscustomobject]@{ ExitCode = $code; Out = $out; Err = $err.Trim(); TimedOut = $timedOut }
    } finally {
        $process.Dispose()
    }
}

function Send-Body {
    # The JSON body to the server. A failure is logged and is never raised: the next
    # boundary sends a fresh capture, and the workbook must still get this one.
    param([byte[]]$Body)
    if ($NoSend) {
        Write-Log ('not sent (-NoSend): ' + $Body.Length + ' bytes')
        return
    }
    try {
        $result = Invoke-Ssh 'ingest' $Body
        if ($result.TimedOut) {
            Write-Log ('send timed out after ' + $SshTimeoutSec + ' seconds; ssh was stopped')
        } elseif ($result.ExitCode -ne 0) {
            Write-Log ('send exit ' + $result.ExitCode + ': ' + $result.Err)
        } else {
            Write-Log ('sent ' + $Body.Length + ' bytes: ' + $result.Out.Trim())
        }
    } catch {
        Write-Log ('send failed: ' + $_.Exception.Message)
    }
}

# -- the plan ------------------------------------------------------------------

function Get-CapturePlan {
    if ($PlanFile) {
        $text = [IO.File]::ReadAllText((Resolve-UserPath $PlanFile), $Utf8NoBom)
    } else {
        $result = Invoke-Ssh 'plan' $null
        if ($result.ExitCode -ne 0) {
            throw ('ssh plan exit ' + $result.ExitCode + ': ' + $result.Err)
        }
        $text = $result.Out
    }
    $found = $text | ConvertFrom-Json
    foreach ($field in @('expiry', 'date_text', 'opens', 'valid_until', 'interval_sec',
            'min_fill_fraction', 'max_snapshot_kb', 'instruments')) {
        if ($found.PSObject.Properties.Name -notcontains $field) {
            throw ('the plan has no ' + $field)
        }
    }
    return $found
}

function Update-PlanSettings {
    # The PC's tunables from the plan. One the plan lacks keeps its stated default.
    param($Fetched)
    $names = @($Fetched.PSObject.Properties | ForEach-Object { $_.Name })
    foreach ($key in $PlanSettings.Keys) {
        $number = if ($names -contains $key) { ConvertTo-Reading ([string]$Fetched.$key) } else { $null }
        if ($null -ne $number) {
            Set-Variable -Name $PlanSettings[$key] -Scope Script -Value $number
        } elseif (-not $script:DefaultedSettings.ContainsKey($key)) {
            $script:DefaultedSettings[$key] = $true
            $held = Get-Variable -Name $PlanSettings[$key] -Scope Script -ValueOnly
            Write-Log ('the plan has no ' + $key + '; using ' + $held)
        }
    }
    Update-WatchdogLimit
}

function Update-Plan {
    # At the start and whenever valid_until passes. On failure the old plan is NOT
    # kept: past valid_until its expiry is over. A failure waits longer each time,
    # because a dozen ssh connections in a few minutes can lock the operator out.
    $now = Get-EpochSeconds
    if ($null -ne $script:Plan -and $now -lt [double]$script:Plan.valid_until) { return }
    $previous = if ($null -ne $script:Plan) { [string]$script:Plan.expiry } else { '' }
    $script:Plan = $null
    if ($now -lt $script:PlanRetryAt) { return }
    $fetched = $null
    $why = ''
    try {
        $fetched = Get-CapturePlan
        if ($now -ge [double]$fetched.valid_until) {
            $why = 'the plan for ' + $fetched.expiry + ' is already past its end'
            $fetched = $null
        }
    } catch {
        $why = $_.Exception.Message
    }
    if ($null -eq $fetched) {
        $script:PlanRetryDelay = if ($script:PlanRetryDelay -gt 0) {
            [math]::Min($script:PlanRetryDelay * 2, $PlanRetryMaxSec)
        } else { $NoPlanRetrySec }
        $script:PlanRetryAt = $now + $script:PlanRetryDelay
        Write-Log ('no plan: ' + $why + '; next try in ' + $script:PlanRetryDelay + ' seconds')
        return
    }
    $script:PlanRetryDelay = 0.0
    $script:PlanRetryAt = 0.0
    Update-PlanSettings $fetched
    $script:Plan = $fetched
    Write-Log ('plan: expiry ' + $fetched.expiry + ', every ' + $fetched.interval_sec + ' seconds')
    if ($previous -and $previous -ne [string]$fetched.expiry) {
        # Rewritten without restarting Excel: the ladders are built afresh for the
        # new expiry, and nothing stays subscribed to the old one until then.
        Clear-Ladders
    }
}

# -- Excel ---------------------------------------------------------------------

function Get-WindowProcessId {
    param([long]$Handle)
    Add-Type -Namespace TosCapture -Name Native -MemberDefinition (
        '[DllImport("user32.dll")] public static extern uint ' +
        'GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);')
    $owner = [uint32]0
    [void][TosCapture.Native]::GetWindowThreadProcessId([IntPtr]$Handle, [ref]$owner)
    return [int]$owner
}

function New-OwnedRecord {
    # What identifies a copy this run owns. Reading .Handle makes the Process object
    # hold the handle open, and Windows does not give an id with an open handle to
    # another process, however long this copy has been dead.
    param([Diagnostics.Process]$Process, [string]$Name = 'EXCEL', [string]$Switch = '/automation')
    [void]$Process.Handle
    return [pscustomobject]@{
        Process = $Process; Id = $Process.Id; StartTime = $Process.StartTime
        Name = $Name; Switch = $Switch
    }
}

function Stop-OwnedExcel {
    # The ONE way an Excel is stopped, on either thread, so it takes everything it
    # needs as an argument and says what it did rather than logging. An id that is
    # not the copy recorded (another start time, name or command line) is left alone:
    # it could be the operator's, with unsaved work.
    param($Owned)
    if ($null -eq $Owned) { return 'no Excel to stop' }
    $id = $Owned.Id
    $running = Get-Process -Id $id -ErrorAction SilentlyContinue
    if ($null -eq $running) { return 'process ' + $id + ' had already gone' }
    $found = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $id)
    $command = if ($null -ne $found) { [string]$found.CommandLine } else { '' }
    if ($running.ProcessName -ne $Owned.Name -or $running.StartTime -ne $Owned.StartTime -or
        $command -notmatch [regex]::Escape($Owned.Switch)) {
        return 'process ' + $id + ' is not the copy this run started; left alone'
    }
    Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
    return 'process ' + $id + ' stopped'
}

function Write-ExcelIdFile {
    # The id and the start time: enough to know the same copy after a restart.
    param($Owned)
    $text = [string]$Owned.Id + ' ' + $Owned.StartTime.ToUniversalTime().Ticks
    [IO.File]::WriteAllText($ExcelIdFile, $text, $Utf8NoBom)
}

function Stop-LeftoverExcel {
    # A run stopped from outside (the task ended, a log-off) has no finally. Its Excel
    # is known by what it recorded, and is stopped only if it is still that copy.
    if (-not (Test-Path -LiteralPath $ExcelIdFile)) { return }
    try {
        $parts = [IO.File]::ReadAllText($ExcelIdFile).Trim() -split ' '
        if ($parts.Length -eq 2) {
            $started = (New-Object DateTime ([long]$parts[1]), ([DateTimeKind]::Utc)).ToLocalTime()
            $recorded = [pscustomobject]@{
                Process = $null; Id = [int]$parts[0]; StartTime = $started
                Name = 'EXCEL'; Switch = '/automation'
            }
            Write-Log ('a previous run''s Excel: ' + (Stop-OwnedExcel $recorded))
        }
        Remove-Item -LiteralPath $ExcelIdFile -Force
    } catch {
        Write-Log ('could not check for a leftover Excel: ' + $_.Exception.Message)
    }
}

function Start-CaptureExcel {
    # Started first, then attached: COM cannot be trusted to start it. The object COM
    # hands back is checked against the process id, so the capture can only ever
    # work in a copy made for it.
    $process = Start-Process -FilePath 'EXCEL.EXE' -ArgumentList '/automation -Embedding' -WindowStyle Hidden -PassThru
    $script:Owned = New-OwnedRecord $process
    $script:Watch.Owned = $script:Owned
    Write-ExcelIdFile $script:Owned
    Write-Log ('Excel process ' + $process.Id + ' started')
    try {
        [void]$process.WaitForInputIdle($ExcelIdleWaitMs)
    } catch {
        Write-Verbose ('no idle wait: ' + $_.Exception.Message)
    }
    Start-Sleep -Seconds $ExcelAttachPauseSec

    $attached = New-Object -ComObject Excel.Application
    $owner = Get-WindowProcessId $attached.Hwnd
    if ($owner -ne $process.Id) {
        # Measured on a cold start: this copy was not ready, and COM launched another
        # for this very call. That one is as private as this one, and it is the one
        # connected, so it is kept and the idle copy is stopped. An Excel embedded in
        # another program can carry the same switch, so a copy holding a workbook or
        # showing a window is not kept. Whatever is not kept is let go, never touched.
        $other = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $owner)
        $madeForThis = $null -ne $other -and $other.CommandLine -match '/automation'
        if ($madeForThis -and ($attached.Workbooks.Count -ne 0 -or $attached.Visible)) {
            $madeForThis = $false
        }
        if (-not $madeForThis) {
            Release-Com $attached
            throw ('COM attached to Excel process ' + $owner + ', which this run did not start')
        }
        Write-Log ('COM started Excel process ' + $owner + '; using it. Stopping the idle one: ' + (Stop-OwnedExcel $script:Owned))
        $script:Owned.Process.Dispose()
        $script:Owned = New-OwnedRecord ([Diagnostics.Process]::GetProcessById($owner))
        $script:Watch.Owned = $script:Owned
        Write-ExcelIdFile $script:Owned
    }
    # Before any workbook: a file the operator double-clicks is handed to a running
    # Excel, and a hidden copy could be that Excel. It would then open out of sight
    # and die with this copy. Excel saves this value on a clean exit, where it would
    # make the operator's own double-clicks open an empty window, so this copy must
    # never exit cleanly: see Stop-CaptureExcel.
    $attached.IgnoreRemoteRequests = $true
    $attached.Visible = $false
    $attached.DisplayAlerts = $false
    $attached.AskToUpdateLinks = $false
    $attached.ScreenUpdating = $false
    $script:Excel = $attached
    $script:Book = $attached.Workbooks.Add()
    # Force-stopping this copy must not offer the operator a "recovered" workbook.
    $script:Book.EnableAutoRecover = $false
    $attached.Calculation = $XlCalculationAutomatic
}

function Stop-CaptureExcel {
    # By process id, and before anything else, with no Quit(). Quit() has left hidden
    # copies behind, and it once crashed this Excel on its way out (an access violation
    # in combase.dll, 2026-10-31). Windows then restarted the crashed Excel with
    # /restore: a VISIBLE window on the operator's desktop. A clean exit would also
    # save IgnoreRemoteRequests into the operator's own settings. Stopping the process
    # is neither a crash nor a clean exit, so nothing restarts it and nothing is saved,
    # and the workbook it holds was never saved, so nothing is lost. The operator's
    # Excel has a different id and is never looked at. The COM objects are released
    # afterwards, against a server that is gone.
    if ($null -ne $script:Owned) {
        $script:Watch.Owned = $null
        Write-Log ('Excel ' + (Stop-OwnedExcel $script:Owned))
        if ($null -ne $script:Owned.Process) { $script:Owned.Process.Dispose() }
        $script:Owned = $null
    }
    foreach ($sheet in @($script:Sheets.Values)) { Release-Com $sheet }
    $script:Sheets = @{}
    $script:Ladders = @{}
    Release-Com $script:Book
    $script:Book = $null
    Release-Com $script:Excel
    $script:Excel = $null
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
    if (Test-Path -LiteralPath $ExcelIdFile) {
        Remove-Item -LiteralPath $ExcelIdFile -Force -ErrorAction SilentlyContinue
    }
}

function Write-RestoreNotice {
    # Windows brings back a crashed Excel with /restore, as a visible window. It is
    # not this run's copy, so it is left alone, but the operator's first question is
    # where it came from.
    try {
        $found = @(Get-CimInstance Win32_Process -Filter "Name='EXCEL.EXE'" | Where-Object { $_.CommandLine -match '/restore' })
        foreach ($item in $found) {
            if (-not $script:RestoreSeen.ContainsKey($item.ProcessId)) {
                $script:RestoreSeen[$item.ProcessId] = $true
                Write-Log ('Excel process ' + $item.ProcessId + ' is a /restore window: Windows has brought back an Excel that crashed. It is left alone.')
            }
        }
    } catch {
        Write-Verbose ('no look for a restored Excel: ' + $_.Exception.Message)
    }
}

function Test-ExcelAlive {
    if ($null -eq $script:Excel -or $null -eq $script:Owned) { return $false }
    if ($script:Owned.Process.HasExited) { return $false }
    try {
        return [bool]($script:Excel.Hwnd)
    } catch {
        # Excel being busy is alive; a dead RPC server is not.
        return (Test-ExcelBusy $_.Exception.Message)
    }
}

# -- the watchdog ----------------------------------------------------------------

# A COM call has no timeout, so an Excel that stops answering would hold the loop for
# ever, and a hung process has not failed, so the task does not restart it. This runs
# on its own thread and reads the heartbeat. Stopping the Excel makes the loop's stuck
# call throw, and the existing restart path takes over. If the call does not throw,
# the second stage ends this process: .Kill(), because an orderly exit can wait on the
# very thread that is stuck. The next start stops any Excel left behind, by its record,
# and the mutex of a killed process is free.
$WatchdogBody = @'
$firedAt = 0.0
$stoppedAt = 0.0
while (-not $Watch.Stop) {
    Start-Sleep -Seconds $PollSec
    try {
        $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
        $age = $now - $Watch.Beat
        if ($stoppedAt -gt 0 -and $Watch.Beat -gt $stoppedAt) { $stoppedAt = 0.0 }
        if ($age -gt $Watch.LimitSec -and ($now - $firedAt) -gt $Watch.LimitSec) {
            $firedAt = $now
            if ($stoppedAt -eq 0) { $stoppedAt = $now }
            $did = Stop-OwnedExcel $Watch.Owned
            $line = [DateTimeOffset]::Now.ToString('yyyy-MM-dd HH:mm:ss zzz') + ' watchdog: no heartbeat for ' +
                [int]$age + ' seconds (limit ' + $Watch.LimitSec + '): ' + $did + "`r`n"
            [IO.File]::AppendAllText($Watch.LogPath, $line)
        }
        if ($stoppedAt -gt 0 -and ($now - $stoppedAt) -gt $Watch.ExitAfterSec) {
            $line = [DateTimeOffset]::Now.ToString('yyyy-MM-dd HH:mm:ss zzz') + ' watchdog: still no heartbeat ' +
                [int]($now - $stoppedAt) + ' seconds after the stop (limit ' + $Watch.ExitAfterSec + '): ending this process; its task starts another' + "`r`n"
            [IO.File]::AppendAllText($Watch.LogPath, $line)
            [Diagnostics.Process]::GetCurrentProcess().Kill()
        }
    } catch {
        try { [IO.File]::AppendAllText($Watch.LogPath, 'watchdog failed: ' + $_.Exception.Message + "`r`n") } catch { $null = $_ }
    }
}
'@

function Update-Heartbeat {
    $script:Watch.Beat = Get-EpochSeconds
}

function Update-WatchdogLimit {
    # Never below a whole ssh, whatever the plan says: that would kill a healthy Excel.
    $script:Watch.LimitSec = [math]::Max([double]$HangLimitSec, 2 * [double]$SshTimeoutSec + $SshHangMarginSec)
    $script:Watch.ExitAfterSec = [double]$HangExitAfterSec
}

function Start-Watchdog {
    Update-Heartbeat
    Update-WatchdogLimit
    $script:Watch.Stop = $false
    # The check is passed by its text: a runspace does not share this one's functions.
    $text = 'param($Watch, $PollSec)' + "`n" + 'function Stop-OwnedExcel {' +
        ${function:Stop-OwnedExcel}.ToString() + "}`n" + $WatchdogBody
    $shell = [powershell]::Create()
    [void]$shell.AddScript($text).AddArgument($script:Watch).AddArgument($WatchdogPollSec)
    [void]$shell.BeginInvoke()
    $script:WatchShell = $shell
}

function Stop-Watchdog {
    $script:Watch.Stop = $true
    if ($null -ne $script:WatchShell) {
        try { $script:WatchShell.Stop() } catch { Write-Verbose $_.Exception.Message }
        $script:WatchShell.Dispose()
        $script:WatchShell = $null
    }
}

function Test-ExcelBusy {
    # "Call was rejected by callee" and "the application is busy": ask again shortly.
    param([string]$Message)
    return $Message -match '0x8001010A|0x800AC472'
}

function Get-InstrumentSheet {
    param([string]$Root)
    if ($script:Sheets.ContainsKey($Root)) { return $script:Sheets[$Root] }
    $sheet = $script:Book.Worksheets.Add()
    $sheet.Name = $Root
    $script:Sheets[$Root] = $sheet
    return $sheet
}

function Write-Formulas {
    # One bulk assignment: a cell at a time would take minutes at this size.
    param($Sheet, [object[,]]$Matrix)
    $rows = $Matrix.GetLength(0)
    $columns = $Matrix.GetLength(1)
    $attempt = 0
    while ($true) {
        $cells = $null
        $target = $null
        try {
            $cells = $Sheet.Cells
            [void]$cells.ClearContents()
            $target = $Sheet.Range($cells.Item(1, 1), $cells.Item($rows, $columns))
            $target.Formula = $Matrix
            return
        } catch {
            $attempt++
            if ($attempt -ge $ExcelBusyAttempts -or -not (Test-ExcelBusy $_.Exception.Message)) { throw }
            Start-Sleep -Milliseconds $ExcelBusyPauseMs
        } finally {
            Release-Com $target
            Release-Com $cells
        }
    }
}

function Read-Block {
    # The sheet's top-left rows x columns as a 1-based [object[,]]. The leading comma
    # keeps PowerShell from flattening the array on the way out of the function.
    param($Sheet, [int]$Rows, [int]$Columns)
    $attempt = 0
    while ($true) {
        $cells = $null
        $target = $null
        try {
            $cells = $Sheet.Cells
            $target = $Sheet.Range($cells.Item(1, 1), $cells.Item($Rows, $Columns))
            $values = $target.Value2
            if ($values -isnot [array]) {
                $single = [Array]::CreateInstance([object], [int[]]@(1, 1), [int[]]@(1, 1))
                $single.SetValue($values, 1, 1)
                $values = $single
            }
            return , $values
        } catch {
            $attempt++
            if ($attempt -ge $ExcelBusyAttempts -or -not (Test-ExcelBusy $_.Exception.Message)) { throw }
            Start-Sleep -Milliseconds $ExcelBusyPauseMs
        } finally {
            Release-Com $target
            Release-Com $cells
        }
    }
}

function Wait-Filled {
    # True once the sheet has settled (see $FillSettleSec), false at the cap.
    param($Sheet, [int]$Rows, [int]$Columns)
    $deadline = (Get-EpochSeconds) + $FillWaitMaxSec
    $last = ''
    $since = Get-EpochSeconds
    while ((Get-EpochSeconds) -lt $deadline) {
        $values = Read-Block $Sheet $Rows $Columns
        # Excel just answered, so this is a sign of life.
        Update-Heartbeat
        $filled = 0
        $nonZero = 0
        foreach ($cell in $values) {
            if ($cell -is [double]) {
                $filled++
                if ($cell -ne 0) { $nonZero++ }
            } elseif ($cell -is [string]) {
                $text = $cell.Trim()
                if ($text -and $text -ne 'N/A') {
                    $filled++
                    if ($text -ne '0') { $nonZero++ }
                }
            }
        }
        $signature = [string]$filled + '/' + $nonZero
        $now = Get-EpochSeconds
        if ($signature -ne $last) {
            $last = $signature
            $since = $now
        } elseif ($filled -gt 0 -and ($now - $since) -ge $FillSettleSec) {
            return $true
        }
        Start-Sleep -Milliseconds $FillPollMs
    }
    return $false
}

# -- finding the series and writing the ladder ---------------------------------

function Read-OneValue {
    # Single row of symbols' MARKs on the sheet: the first positive one, or $null.
    param($Sheet, [string[]]$Symbols)
    $matrix = New-Object 'object[,]' 1, $Symbols.Length
    for ($i = 0; $i -lt $Symbols.Length; $i++) {
        $matrix[0, $i] = Get-RtdFormula 'MARK' $Symbols[$i]
    }
    Write-Formulas $Sheet $matrix
    [void](Wait-Filled $Sheet 1 $Symbols.Length)
    $values = Read-Block $Sheet 1 $Symbols.Length
    for ($i = 1; $i -le $Symbols.Length; $i++) {
        $price = ConvertTo-Reading $values[1, $i]
        if ($null -ne $price -and $price -gt 0) { return $price }
    }
    return $null
}

function Find-Series {
    # Each candidate's DESCRIPTION at a few round strikes. Kept: a series that names
    # itself, states the plan's date and is not morning settled. Everything seen goes
    # in the log, so a day when nothing matches says what the contracts did say.
    param($Sheet, $Spec, [string]$Root, [double]$Price)
    $candidates = @($Spec.candidates)
    $spacing = [double]$Spec.strike_step * $ProbeRoundSteps
    $centre = [math]::Round($Price / $spacing) * $spacing
    $strikes = @()
    for ($k = -$ProbeReach; $k -le $ProbeReach; $k++) { $strikes += $centre + $k * $spacing }

    $matrix = New-Object 'object[,]' $candidates.Length, $strikes.Length
    for ($r = 0; $r -lt $candidates.Length; $r++) {
        for ($c = 0; $c -lt $strikes.Length; $c++) {
            $symbol = Get-OptionSymbol $candidates[$r] 'C' $strikes[$c] $Spec.exchange
            $matrix[$r, $c] = Get-RtdFormula 'DESCRIPTION' $symbol
        }
    }
    Write-Formulas $Sheet $matrix
    [void](Wait-Filled $Sheet $candidates.Length $strikes.Length)
    $values = Read-Block $Sheet $candidates.Length $strikes.Length

    $kept = New-Object System.Collections.ArrayList
    for ($r = 0; $r -lt $candidates.Length; $r++) {
        $series = [string]$candidates[$r]
        $shown = $false
        for ($c = 0; $c -lt $strikes.Length; $c++) {
            $text = $values[($r + 1), ($c + 1)]
            if ($text -isnot [string] -or $text.Trim() -eq 'N/A' -or -not $text.Trim()) { continue }
            $text = $text.Trim()
            if (-not $shown) {
                Write-Log ($Root + ' ' + $series + ' reads: ' + $text)
                $shown = $true
            }
            if ($text -match 'AM Settled') { break }
            if (-not (Test-StatesDate $text $script:Plan.date_text)) { break }
            if ((Get-DescribedSeries $text) -ne $series.ToUpperInvariant()) { break }
            if ($null -eq (Get-DescribedFuture $text)) { break }
            [void]$kept.Add([pscustomobject]@{ Series = $series; Description = $text; Strike = $strikes[$c] })
            break
        }
    }
    return , $kept.ToArray()
}

function Build-Ladder {
    # Raises with the reason this instrument cannot be captured; the caller names it.
    param([string]$Root, $Spec)
    $sheet = Get-InstrumentSheet $Root

    # Only to know where to look: no number from here is ever sent. The price that
    # goes on the wire is read below from the future the contracts themselves name.
    $last = @($Spec.candidates)[-1]
    $guess = Read-OneValue $sheet @(('/' + $Root + ':' + $Spec.exchange), ('/' + $last + ':' + $Spec.exchange))
    if ($null -eq $guess) {
        throw ('every price cell reads N/A; ThinkorSwim may be logged out')
    }

    $found = @(Find-Series $sheet $Spec $Root $guess)
    if ($found.Length -eq 0) {
        throw ('no series states ' + $script:Plan.date_text + ' (tried ' + (@($Spec.candidates) -join ' ') + ')')
    }

    # One chain, one future. A series written on another is left out here rather
    # than being refused by the server, because the price is read from just one.
    $future = Get-DescribedFuture $found[0].Description
    $series = @($found | Where-Object { (Get-DescribedFuture $_.Description) -eq $future })
    foreach ($item in $found) {
        if ($series -notcontains $item) {
            Write-Log ($Root + ' ' + $item.Series + ' left out: it is written on another future')
        }
    }

    $price = Read-OneValue $sheet @($future)
    if ($null -eq $price) { throw ($future + ' reads no price') }

    $step = [double]$Spec.strike_step
    $reach = $price * [double]$Spec.strike_width_pct / 100.0
    $first = [math]::Ceiling(($price - $reach) / $step)
    $final = [math]::Floor(($price + $reach) / $step)
    $strikes = New-Object System.Collections.ArrayList
    for ($k = $first; $k -le $final; $k++) { [void]$strikes.Add($k * $step) }

    # Row 1: the future's MARK, then one DESCRIPTION per series. Then one row per
    # contract, in the order of $rows.
    $rows = New-Object System.Collections.ArrayList
    foreach ($item in $series) {
        foreach ($side in @('C', 'P')) {
            foreach ($strike in $strikes) {
                [void]$rows.Add([pscustomobject]@{ Series = $item.Series; Side = $side; Strike = [double]$strike })
            }
        }
    }
    $columns = [math]::Max($RtdFields.Length, 1 + $series.Length)
    $matrix = New-Object 'object[,]' ($rows.Count + 1), $columns
    $matrix[0, 0] = Get-RtdFormula 'MARK' $future
    for ($i = 0; $i -lt $series.Length; $i++) {
        $symbol = Get-OptionSymbol $series[$i].Series 'C' $series[$i].Strike $Spec.exchange
        $matrix[0, ($i + 1)] = Get-RtdFormula 'DESCRIPTION' $symbol
    }
    for ($r = 0; $r -lt $rows.Count; $r++) {
        $row = $rows[$r]
        $symbol = Get-OptionSymbol $row.Series $row.Side $row.Strike $Spec.exchange
        for ($c = 0; $c -lt $RtdFields.Length; $c++) {
            $matrix[($r + 1), $c] = Get-RtdFormula $RtdFields[$c] $symbol
        }
    }
    Write-Formulas $sheet $matrix
    $settled = Wait-Filled $sheet ($rows.Count + 1) $columns
    Write-Log ($Root + ' ladder: ' + (@($series | ForEach-Object { $_.Series }) -join ' ') + ' on ' + $future + ' at ' + $price +
        ', ' + $rows.Count + ' contracts' + $(if ($settled) { '' } else { ' (still filling at the cap)' }))

    return @{
        Root = $Root; Sheet = $sheet; Future = $future; Series = $series; Rows = $rows
        Columns = $columns; Centre = $price; Reach = $reach; MostRead = 0
        Settled = $settled; BuiltSlot = [math]::Floor((Get-EpochSeconds) / [double]$script:Plan.interval_sec)
    }
}

function Clear-Ladders {
    $script:Ladders = @{}
    foreach ($sheet in @($script:Sheets.Values)) {
        try {
            $cells = $sheet.Cells
            [void]$cells.ClearContents()
            Release-Com $cells
        } catch {
            Write-Log ('could not clear a sheet: ' + $_.Exception.Message)
        }
    }
}

# -- getting ready for a capture -------------------------------------------------

function Invoke-Prepare {
    # Safe to call at any time: it does only what the last call left undone. Nothing
    # here sends; what is wrong is held for the capture to report.
    #
    # -KeepTime is the call at the boundary itself. Finding a series can take half a
    # minute, and an instrument still without one must not hold back the capture of
    # the one that is ready, so it waits for the next call before the next boundary.
    # With nothing ready there is nothing to hold back, which also covers the first
    # boundary after the session opens and an Excel that died during the wait.
    param([switch]$KeepTime)
    $script:GeneralProblem = ''
    Update-Plan
    if ($null -eq $script:Plan) {
        $script:InSession = $false
        return
    }
    $now = Get-EpochSeconds
    $script:InSession = [bool]$Once -or ($now -ge [double]$script:Plan.opens -and $now -lt [double]$script:Plan.valid_until)
    if (-not $script:InSession) { return }
    Write-RestoreNotice

    if (-not (Test-ExcelAlive)) {
        if ($null -ne $script:Owned) { Write-Log 'Excel has gone; restarting it' }
        Stop-CaptureExcel
        for ($attempt = 1; $attempt -le $ExcelStartAttempts; $attempt++) {
            try {
                Update-Heartbeat
                Start-CaptureExcel
                break
            } catch {
                Write-Log ('Excel start ' + $attempt + ' failed: ' + $_.Exception.Message)
                Stop-CaptureExcel
            }
        }
        if ($null -eq $script:Excel) {
            $script:GeneralProblem = 'Excel did not start on the capture PC.'
            return
        }
    }

    # Looked at, never started. Oldest first: if two share the name the same one is
    # picked every time, so their order cannot read as a restart.
    $thinkorswim = @(Get-Process -Name 'thinkorswim' -ErrorAction SilentlyContinue | Sort-Object StartTime, Id)
    if ($thinkorswim.Length -eq 0) {
        $script:GeneralProblem = 'ThinkorSwim is not running on the capture PC.'
        $script:Ladders = @{}
        return
    }
    $currentId = ($thinkorswim | Select-Object -First 1).Id
    if ($null -ne $script:ThinkorswimId -and $script:ThinkorswimId -ne $currentId) {
        # A restarted ThinkorSwim is a new RTD server, and Excel still holds the dead
        # one. A fresh Excel is the start known to work.
        Write-Log 'ThinkorSwim restarted; restarting Excel'
        Stop-CaptureExcel
        $script:ThinkorswimId = $currentId
        Invoke-Prepare -KeepTime:$KeepTime
        return
    }
    $script:ThinkorswimId = $currentId

    if ($KeepTime -and $script:Ladders.Count -gt 0) { return }
    $script:Problems = @{}
    foreach ($entry in $script:Plan.instruments.PSObject.Properties) {
        $root = $entry.Name
        if ($script:Ladders.ContainsKey($root)) { continue }
        Update-Heartbeat
        try {
            $script:Ladders[$root] = Build-Ladder $root $entry.Value
        } catch {
            $script:Problems[$root] = $_.Exception.Message
            Write-Log ($root + ' not ready: ' + $_.Exception.Message + ' | ' + ($_.ScriptStackTrace -replace '\s+', ' '))
        }
    }
}

function Wait-Sleeping {
    # A sleep in short pieces, each with a heartbeat: a long wait is not a hang.
    param([double]$Seconds)
    $until = (Get-EpochSeconds) + $Seconds
    $left = $Seconds
    while ($left -gt 0) {
        Update-Heartbeat
        Start-Sleep -Milliseconds ([math]::Max(10, [int]([math]::Min($left, $SleepSliceSec) * 1000)))
        $left = $until - (Get-EpochSeconds)
    }
    Update-Heartbeat
}

function Wait-NextBoundary {
    # Boundaries counted from the epoch, so a restart stays on the same clock.
    if ($null -eq $script:Plan) {
        Wait-Sleeping ([math]::Max(1, $script:PlanRetryAt - (Get-EpochSeconds)))
        return
    }
    $interval = [double]$script:Plan.interval_sec
    $now = Get-EpochSeconds
    $next = ([math]::Floor($now / $interval) + 1) * $interval
    while ($now -lt $next) {
        # A sleep can wake a little early, and that would capture this boundary twice.
        Wait-Sleeping ($next - $now)
        $now = Get-EpochSeconds
    }
}

# -- one capture ---------------------------------------------------------------

function Test-RowReadsNothing {
    # True when no field holds anything but a zero or an absence.
    param([object[]]$Readings)
    foreach ($reading in $Readings) {
        if ($null -ne $reading -and $reading -ne 0) { return $false }
    }
    return $true
}

function Read-Snapshot {
    # The instrument's snapshot, or $null with the reason in $script:Problems.
    param($Ladder, $Plan)
    $root = $Ladder.Root
    # A ladder that reached the fill cap straight before this read is half a chain,
    # and the first capture of a session has nothing to be compared with. By the next
    # boundary it has had minutes.
    if (-not $Ladder.Settled -and [math]::Floor((Get-EpochSeconds) / [double]$Plan.interval_sec) -eq $Ladder.BuiltSlot) {
        $script:Problems[$root] = 'the sheet was still filling when it was written; it is read at the next boundary'
        return $null
    }
    $rowCount = $Ladder.Rows.Count + 1
    # Taken at the read: this is the moment the values describe.
    $capturedAt = Get-EpochSeconds
    $values = Read-Block $Ladder.Sheet $rowCount $Ladder.Columns

    $price = ConvertTo-Reading $values[1, 1]
    if ($null -eq $price -or $price -le 0) {
        $script:Problems[$root] = $Ladder.Future + ' reads no price'
        [void]$script:Ladders.Remove($root)
        return $null
    }

    # A series' terms are read afresh each time; one that no longer states them is
    # dropped for this capture. The server checks the same text again.
    $descriptions = @{}
    for ($i = 0; $i -lt $Ladder.Series.Length; $i++) {
        $text = $values[1, ($i + 2)]
        $name = $Ladder.Series[$i].Series
        if ($text -is [string] -and (Test-StatesDate $text $Plan.date_text) -and $text -notmatch 'AM Settled') {
            $descriptions[$name] = $text.Trim()
        } else {
            Write-Log ($root + ' ' + $name + ' dropped from this capture: its description reads ' + $text)
        }
    }

    $contracts = New-Object System.Collections.ArrayList
    $tried = 0
    $line = 1
    foreach ($row in $Ladder.Rows) {
        $line++
        if (-not $descriptions.ContainsKey($row.Series)) { continue }
        $tried++
        $mark = ConvertTo-Reading $values[$line, 1]
        $bid = ConvertTo-Reading $values[$line, 2]
        $ask = ConvertTo-Reading $values[$line, 3]
        $volume = ConvertTo-Reading $values[$line, 4]
        $openInt = ConvertTo-Reading $values[$line, 5]
        $iv = ConvertTo-PercentReading $values[$line, 6]
        $delta = ConvertTo-Reading $values[$line, 7]
        # N/A on every field is a contract that is not listed. A listed one can read
        # N/A on some fields, and those travel as absent, never as zero. Nothing but
        # zeros is a cell ThinkorSwim has not answered yet: sending it would put a false
        # 0 open interest on the wire.
        if (Test-RowReadsNothing @($mark, $bid, $ask, $volume, $openInt, $iv, $delta)) { continue }
        [void]$contracts.Add([ordered]@{
                series = $row.Series; side = $row.Side; strike = $row.Strike
                mark = $mark; bid = $bid; ask = $ask; volume = $volume
                open_int = $openInt; iv = $iv; delta = $delta
            })
    }

    if ($contracts.Count -eq 0) {
        $script:Problems[$root] = 'every contract reads N/A; ThinkorSwim may be logged out'
        [void]$script:Ladders.Remove($root)
        return $null
    }

    # Against what was tried, for a first read with nothing to compare with.
    if ($contracts.Count -lt $MinReadFraction * $tried) {
        $script:Problems[$root] = 'read ' + $contracts.Count + ' of ' + $tried + ' contracts tried; not sent'
        [void]$script:Ladders.Remove($root)
        return $null
    }

    # Contracts do not delist within a session, so a large fall against the most this
    # ladder has read is a sheet that has lost its feed, not a thinner chain. Rewriting
    # is what might bring the feed back; the server's repeated-count rule decides what
    # is done with the smaller count if it stays.
    $least = [double]$Plan.min_fill_fraction * $Ladder.MostRead
    if ($contracts.Count -lt $least) {
        $script:Problems[$root] = 'read ' + $contracts.Count + ' contracts against ' + $Ladder.MostRead + ' earlier; not sent'
        [void]$script:Ladders.Remove($root)
        return $null
    }
    if ($contracts.Count -gt $Ladder.MostRead) { $Ladder.MostRead = $contracts.Count }

    if ([math]::Abs($price - $Ladder.Centre) -gt $RecentreShare * $Ladder.Reach) {
        Write-Log ($root + ' price ' + $price + ' has moved from ' + $Ladder.Centre + '; rewriting the ladder')
        [void]$script:Ladders.Remove($root)
    }

    return [ordered]@{
        instrument = $root
        expiry = [string]$Plan.expiry
        captured_at = $capturedAt
        descriptions = $descriptions
        underlying_symbol = $Ladder.Future
        underlying_price = $price
        contracts = $contracts.ToArray()
    }
}

function Add-ToWorkbook {
    # The dated copy, through this capture's own private Excel, so the operator's
    # Excel is never involved. A file the operator is holding fails the exclusive
    # open and is skipped, never overwritten; Excel's read-only flag is the second
    # check, for one opened between the two lines.
    param($Plan, $Snapshots)
    $folder = if ($WorkbookDir) { Resolve-UserPath $WorkbookDir } else { Join-Path $env:USERPROFILE 'Documents\ToS 0DTE' }
    if (-not (Test-Path -LiteralPath $folder)) {
        [void](New-Item -ItemType Directory -Path $folder -Force)
    }
    $path = Join-Path $folder ([string]$Plan.expiry + '.xlsx')
    $exists = Test-Path -LiteralPath $path
    if ($exists) {
        try {
            $probe = [IO.File]::Open($path, 'Open', 'ReadWrite', 'None')
            $probe.Close()
        } catch {
            Write-Log ('workbook skipped, it is held open: ' + $path)
            return
        }
    }
    $workbooks = $script:Excel.Workbooks
    $book = if ($exists) { $workbooks.Open($path, 0, $false) } else { $workbooks.Add() }
    try {
        if ($exists -and $book.ReadOnly) {
            Write-Log ('workbook skipped, it opened read-only: ' + $path)
            return
        }
        # A new workbook comes with a blank sheet; it goes once the instruments' are in.
        $blank = if ($exists) { $null } else { $book.Worksheets.Item(1) }
        foreach ($snapshot in $Snapshots) { Add-SnapshotRows $book $snapshot }
        if ($null -ne $blank) {
            [void]$blank.Delete()
            Release-Com $blank
        }
        if ($exists) { $book.Save() } else { $book.SaveAs($path, $XlOpenXmlWorkbook) }
    } finally {
        [void]$book.Close($false)
        Release-Com $book
        Release-Com $workbooks
    }
}

function Add-SnapshotRows {
    param($Book, $Snapshot)
    $headers = @('captured_at', 'captured_on_pc_clock', 'underlying_symbol', 'underlying_price',
        'series', 'side', 'strike', 'mark', 'bid', 'ask', 'volume', 'open_int', 'iv_percent', 'delta')
    $sheet = $null
    foreach ($each in $Book.Worksheets) {
        if ($each.Name -eq $Snapshot.instrument) { $sheet = $each }
    }
    if ($null -eq $sheet) {
        $sheet = $Book.Worksheets.Add()
        $sheet.Name = $Snapshot.instrument
    }
    $used = $sheet.UsedRange
    $nextRow = if ($null -eq $sheet.Cells.Item(1, 1).Value2) { 1 } else { $used.Row + $used.Rows.Count }
    $rows = @($Snapshot.contracts)
    $start = if ($nextRow -eq 1) { 1 } else { 0 }
    $matrix = New-Object 'object[,]' ($rows.Length + $start), $headers.Length
    if ($start -eq 1) {
        for ($c = 0; $c -lt $headers.Length; $c++) { $matrix[0, $c] = $headers[$c] }
    }
    # With a T in it Excel leaves the text alone rather than turning it into a serial.
    $clock = [DateTimeOffset]::FromUnixTimeMilliseconds([long]($Snapshot.captured_at * 1000)).ToLocalTime().ToString('yyyy-MM-ddTHH:mm:ss', $Invariant)
    for ($i = 0; $i -lt $rows.Length; $i++) {
        $row = $rows[$i]
        $at = $i + $start
        $matrix[$at, 0] = $Snapshot.captured_at
        $matrix[$at, 1] = $clock
        $matrix[$at, 2] = $Snapshot.underlying_symbol
        $matrix[$at, 3] = $Snapshot.underlying_price
        $matrix[$at, 4] = $row.series
        $matrix[$at, 5] = $row.side
        $matrix[$at, 6] = $row.strike
        $matrix[$at, 7] = $row.mark
        $matrix[$at, 8] = $row.bid
        $matrix[$at, 9] = $row.ask
        $matrix[$at, 10] = $row.volume
        $matrix[$at, 11] = $row.open_int
        $matrix[$at, 12] = $row.iv
        $matrix[$at, 13] = $row.delta
    }
    $cells = $sheet.Cells
    $target = $sheet.Range($cells.Item($nextRow, 1), $cells.Item($nextRow + $rows.Length + $start - 1, $headers.Length))
    $target.Value2 = $matrix
    Release-Com $target
    Release-Com $cells
    Release-Com $used
    Release-Com $sheet
}

function Invoke-Capture {
    # Reads every instrument, sends what read and says why anything did not, then
    # keeps the copy. One instrument failing never blocks the other.
    $script:SnapshotCount = 0
    if ($null -eq $script:Plan -or -not $script:InSession) { return }
    $plan = $script:Plan
    $snapshots = New-Object System.Collections.ArrayList
    $reasons = New-Object System.Collections.ArrayList

    if ($script:GeneralProblem) {
        [void]$reasons.Add($script:GeneralProblem)
    } else {
        $limit = [double]$plan.max_snapshot_kb * 1024
        foreach ($entry in $plan.instruments.PSObject.Properties) {
            $root = $entry.Name
            if (-not $script:Ladders.ContainsKey($root)) {
                $why = if ($script:Problems.ContainsKey($root)) { $script:Problems[$root] } else { 'not ready' }
                [void]$reasons.Add($root + ': ' + $why)
                continue
            }
            Update-Heartbeat
            try {
                $snapshot = Read-Snapshot $script:Ladders[$root] $plan
            } catch {
                # Excel going away shows up here, and the next prepare restarts it.
                [void]$script:Ladders.Remove($root)
                $script:Problems[$root] = 'could not read the sheet: ' + $_.Exception.Message
                $snapshot = $null
            }
            if ($null -eq $snapshot) {
                [void]$reasons.Add($root + ': ' + $script:Problems[$root])
                Write-Log ($root + ' left out: ' + $script:Problems[$root])
                continue
            }
            $size = $Utf8NoBom.GetByteCount(($snapshot | ConvertTo-Json -Depth 5 -Compress))
            if ($size -gt $limit) {
                [void]$reasons.Add($root + ': the snapshot is ' + [int]($size / 1024) + ' KB, over the limit')
                Write-Log ($root + ' left out: ' + $size + ' bytes is over the plan''s limit')
                continue
            }
            [void]$snapshots.Add($snapshot)
            Write-Log ($root + ': ' + @($snapshot.contracts).Length + ' contracts, ' + $size + ' bytes, future ' + $snapshot.underlying_price)
        }
    }

    # '; ' and nothing else: the server splits this on it, to give each instrument's
    # label its own reason. Each sentence can hold a semicolon of its own.
    $reason = $reasons -join '; '
    if ($reason) { Write-Log ('status: ' + $reason) }
    $document = [ordered]@{
        snapshots = $snapshots.ToArray()
        status = [ordered]@{ reason = $reason; at = Get-EpochSeconds }
    }
    $body = $Utf8NoBom.GetBytes(($document | ConvertTo-Json -Depth 6 -Compress))
    if ($OutFile) {
        [IO.File]::WriteAllBytes((Resolve-UserPath $OutFile), $body)
    }
    Update-Heartbeat
    Send-Body $body
    Update-Heartbeat
    $script:SnapshotCount = $snapshots.Count

    # After the send and apart from it: a workbook that fails never costs the capture.
    if ($snapshots.Count -gt 0) {
        try {
            Add-ToWorkbook $plan $snapshots.ToArray()
        } catch {
            Write-Log ('workbook not written: ' + $_.Exception.Message)
        }
    }
}

# -- the loop ------------------------------------------------------------------

function Invoke-Main {
    # One capture at a time on this PC: two would fight over excel.pid and over the
    # workbook, and ThinkorSwim's RTD gains nothing from a second reader.
    $mutex = New-Object Threading.Mutex($false, $MutexName)
    $held = $false
    try {
        $held = $mutex.WaitOne(0)
    } catch [Threading.AbandonedMutexException] {
        $held = $true
    }
    if (-not $held) {
        Write-Log 'another capture is running on this PC; this one stops'
        $script:ExitCode = 2
        return
    }
    try {
        Write-Log ('capture started' + $(if ($Once) { ' (-Once)' } else { '' }))
        Stop-LeftoverExcel
        Start-Watchdog
        if ($Once) {
            Invoke-Prepare
            Invoke-Capture
            if ($script:SnapshotCount -eq 0) { $script:ExitCode = 1 }
            return
        }
        while ($true) {
            Update-Heartbeat
            try {
                Invoke-Prepare
            } catch {
                Write-Log ('prepare failed: ' + $_.Exception.Message)
            }
            Wait-NextBoundary
            try {
                Invoke-Prepare -KeepTime
            } catch {
                # The boundary still reports: silence here reads as a PC that has gone.
                Write-Log ('prepare failed: ' + $_.Exception.Message)
                $script:GeneralProblem = 'The capture could not get ready: ' + $_.Exception.Message
            }
            try {
                Invoke-Capture
            } catch {
                Write-Log ('capture failed: ' + $_.Exception.Message)
            }
        }
    } finally {
        Stop-Watchdog
        Stop-CaptureExcel
        Write-Log 'capture stopped'
        $mutex.ReleaseMutex()
        $mutex.Dispose()
    }
}

# Dot-sourcing loads the functions and runs nothing, which is how they get tested.
if ($MyInvocation.InvocationName -ne '.') {
    Invoke-Main
    exit $script:ExitCode
}
