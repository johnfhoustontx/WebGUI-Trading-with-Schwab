# A by-hand check of the capture's functions: what a source-level test cannot pin.
# Run by hand on the PC: powershell -NoProfile -File check_functions.ps1 -WorkFolder <dir>
#
# It starts no Excel and does not need ThinkorSwim. ssh is replaced by fake_ssh.py, so
# nothing reaches the server. Its log goes under -WorkFolder, not the capture's own.
param(
    [Parameter(Mandatory = $true)][string]$WorkFolder,
    [string]$PythonExe = 'python'
)
$checks = $PSScriptRoot
. (Join-Path $checks '..\tos_capture.ps1') -Once
$ErrorActionPreference = 'Continue'
[void][IO.Directory]::CreateDirectory($WorkFolder)
$WorkDir = $WorkFolder
$LogPath = Join-Path $WorkFolder 'check_functions.log'
$ExcelIdFile = Join-Path $WorkFolder 'check_functions.excel_id'
$script:Watch.LogPath = $LogPath

$fails = 0
function Check([string]$Name, $Got, $Want) {
    $g = if ($null -eq $Got) { '<null>' } else { "$Got" }
    $w = if ($null -eq $Want) { '<null>' } else { "$Want" }
    if ($g -ceq $w) { "ok   $Name" } else { "FAIL $Name : got [$g] want [$w]"; $script:fails++ }
}

# Readings
Check 'na' (ConvertTo-Reading 'N/A') $null
Check 'empty' (ConvertTo-Reading '  ') $null
Check 'null' (ConvertTo-Reading $null) $null
Check 'excel error int' (ConvertTo-Reading ([int]-2146826246)) $null
Check 'double' (ConvertTo-Reading ([double]7600.25)) 7600.25
Check 'text number' (ConvertTo-Reading '7,600.25') 7600.25
Check 'zero is a reading' (ConvertTo-Reading '0') 0
Check 'nan' (ConvertTo-Reading ([double]::NaN)) $null
Check 'percent' (ConvertTo-PercentReading '18.5%') 18.5
Check 'percent spaced' (ConvertTo-PercentReading ' 12.51 % ') 12.51
Check 'percent na' (ConvertTo-PercentReading 'N/A') $null
Check 'bare fraction is absent' (ConvertTo-PercentReading ([double]0.185)) $null
Check 'bare number is absent' (ConvertTo-PercentReading '18.5') $null

# A row of nothing but zeros has not been answered
Check 'all absent' (Test-RowReadsNothing @($null, $null)) $true
Check 'all zero' (Test-RowReadsNothing @([double]0, $null, [double]0)) $true
Check 'one value' (Test-RowReadsNothing @([double]0, [double]1.25)) $false
Check 'a negative delta is a value' (Test-RowReadsNothing @($null, [double]-0.5)) $false

# Terms
$described = '/ESZ26:XCME 1/50 2 NOV 26 (Monday) (Wk1) /E1AX26:XCME 7600 CALL'
Check 'date' (Test-StatesDate $described '2 NOV 26') $true
Check 'date inside longer date' (Test-StatesDate '/ESZ26:XCME 1/50 12 NOV 26 (Thursday) /E2DX26:XCME 7600 CALL' '2 NOV 26') $false
Check 'future' (Get-DescribedFuture $described) '/ESZ26:XCME'
Check 'series' (Get-DescribedSeries $described) 'E1AX26'
Check 'series with a dotted strike' (Get-DescribedSeries '/ESZ26:XCME 1/50 2 NOV 26 /E1AX26:XCME 7602.5 PUT') 'E1AX26'
Check 'na series' (Get-DescribedSeries 'N/A') $null
Check 'option symbol' (Get-OptionSymbol 'ABCX26' 'C' 7600 'XCME') './ABCX26C7600:XCME'
Check 'fractional strike' (Get-OptionSymbol 'ABCX26' 'P' 7602.5 'XCME') './ABCX26P7602.5:XCME'
Check 'formula' (Get-RtdFormula 'MARK' '/ESZ26:XCME') '=RTD("tos.rtd",,"MARK","/ESZ26:XCME")'

# The wire: what ConvertTo-Json does on 5.1
$doc = [ordered]@{
    snapshots = @([ordered]@{
            instrument = 'ES'; captured_at = [double]1793577600.125
            descriptions = @{ E = 'x' }
            contracts = @([ordered]@{ strike = [double]7600; mark = [double]12.25; bid = $null })
        })
    status = [ordered]@{ reason = ''; at = [double]1793577600.5 }
}
Check 'json' ($doc | ConvertTo-Json -Depth 6 -Compress) '{"snapshots":[{"instrument":"ES","captured_at":1793577600.125,"descriptions":{"E":"x"},"contracts":[{"strike":7600,"mark":12.25,"bid":null}]}],"status":{"reason":"","at":1793577600.5}}'
$list = New-Object System.Collections.ArrayList
[void]$list.Add([ordered]@{ a = 1 })
Check 'one row stays an array' ([ordered]@{ c = $list.ToArray() } | ConvertTo-Json -Depth 3 -Compress) '{"c":[{"a":1}]}'
$empty = New-Object System.Collections.ArrayList
Check 'empty stays an array' ([ordered]@{ c = $empty.ToArray() } | ConvertTo-Json -Depth 3 -Compress) '{"c":[]}'

# A plan from an older server: its missing settings keep their defaults, logged once
$hangDefault = $HangLimitSec
Update-PlanSettings ([pscustomobject]@{ fill_settle_sec = 7; hang_limit_sec = 'not a number' })
Update-PlanSettings ([pscustomobject]@{ fill_settle_sec = 7 })
Check 'a sent setting is taken' $FillSettleSec 7
Check 'a bad one keeps its default' $HangLimitSec $hangDefault
$logged = @([IO.File]::ReadAllLines($LogPath) | Where-Object { $_ -match 'the plan has no probe_reach' })
Check 'a missing one is logged once' $logged.Length 1
Check 'the limit never falls under a whole ssh' ($script:Watch.LimitSec -ge 2 * $SshTimeoutSec) $true
$FillSettleSec = 5
$SshTimeoutSec = 60
$NoPlanRetrySec = 60
$PlanRetryMaxSec = 900

# A plan that cannot be fetched: fewer and fewer connections
$script:PlanCalls = 0
$script:PlanAnswer = $null
function Get-CapturePlan {
    $script:PlanCalls++
    if ($null -eq $script:PlanAnswer) { throw 'ssh is down' }
    return $script:PlanAnswer
}
Update-Plan
Check 'first failure waits the shortest' $script:PlanRetryDelay 60
Update-Plan
Check 'nothing is tried before its time' $script:PlanCalls 1
$delays = @()
foreach ($step in 1..6) {
    $script:PlanRetryAt = 0.0
    Update-Plan
    $delays += $script:PlanRetryDelay
}
Check 'each failure waits twice as long, up to the cap' ($delays -join ' ') '120 240 480 900 900 900'
$script:PlanRetryAt = 0.0
$script:PlanAnswer = [pscustomobject]@{ expiry = '2030-01-02'; valid_until = (Get-EpochSeconds) + 1000; interval_sec = 300; instruments = @() }
Update-Plan
Check 'a plan clears the wait' $script:PlanRetryDelay 0
Check 'and is kept' $script:Plan.expiry '2030-01-02'
$script:Plan = $null
Remove-Item Function:\Get-CapturePlan
. ([scriptblock]::Create('function Get-CapturePlan { throw "not used" }'))

# ssh, through the fake
$SshExe = $PythonExe
$fake = Join-Path $checks 'fake_ssh.py'
$SshArguments = '"' + $fake + '"'
$SshTimeoutSec = 4

$bytes = $Utf8NoBom.GetBytes('{"a":1}')
$r = Invoke-Ssh 'echo' $bytes
Check 'no BOM, default console' ($r.Out -match 'first=7b226122') $true

$before = [Console]::InputEncoding
try {
    [Console]::InputEncoding = [Text.Encoding]::UTF8
    $r = Invoke-Ssh 'echo' $bytes
    Check 'no BOM, UTF-8 console' ($r.Out -match 'first=7b226122') $true
    Check 'nothing added at the end' ($r.Out -match 'len=7 ') $true
} catch { "     no console here: $($_.Exception.Message)" } finally {
    try { [Console]::InputEncoding = $before } catch { $null = $_ }
}

$big = New-Object byte[] 3000000
for ($i = 0; $i -lt $big.Length; $i += 1000) { $big[$i] = 65 }
$r = Invoke-Ssh 'flood' $big
Check 'flooded child does not deadlock' ($r.Out -match 'len=3000000') $true
Check 'stderr drained' ($r.Err.Length) 500000
Check 'flood exit' $r.ExitCode 0
Check 'no body' ((Invoke-Ssh 'echo' $null).Out -match 'len=0') $true

$r = Invoke-Ssh 'fail' $bytes
Check 'failure exit' $r.ExitCode 255
Check 'failure says why' $r.Err 'no route to host'

$clock = [Diagnostics.Stopwatch]::StartNew()
$r = Invoke-Ssh 'hang' $bytes
Check 'hang times out' $r.TimedOut $true
Check 'hang within bound' ($clock.Elapsed.TotalSeconds -lt 12) $true

$clock = [Diagnostics.Stopwatch]::StartNew()
$r = Invoke-Ssh 'hang_no_read' $big
Check 'a child that never reads times out' $r.TimedOut $true
Check 'that too within bound' ($clock.Elapsed.TotalSeconds -lt 12) $true
Start-Sleep -Milliseconds 300
$left = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'fake_ssh' })
Check 'no fake left running' $left.Length 0

# A failed send is never raised
$threw = $false
try { Send-Body $bytes } catch { $threw = $true }
Check 'Send-Body quiet' $threw $false
$SshExe = 'no-such-program-anywhere'
$threw = $false
try { Send-Body $bytes } catch { $threw = $true }
Check 'Send-Body quiet with no ssh at all' $threw $false

"failures: $fails"
