# Copy every social card on the public site down to this PC, for posting by hand.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File pull_social_cards.ps1
#
# One ssh connection: social_cards_export.py (beside this file) is piped to the
# server's .venv/bin/python, which answers with one tar of
#   Blog\<published day> <slug>.png / .jpg / .txt
#   Trade Ideas\<date> <HHMM>-<SYMBOL>.png / .jpg / .txt
# (the .txt is the caption the Social Cards page copies). It is unpacked into
# $Dest, replacing a card that changed and NEVER deleting one here: the site keeps
# trade ideas for six days, this folder keeps them for good.
#
# Run daily by the scheduled task "NeuralStrike social cards pull". The task runs
# COPIES of this file and social_cards_export.py from
# C:\Users\john_\NeuralStrike\social-cards-pull\ (not under AppData\Local, which the
# Claude desktop app's writes are redirected away from): re-copy both when either
# changes in tools\. Every run appends a line to pull_social_cards.log beside it and
# exits 1 on failure, which Task Scheduler records as the Last Run Result.
#
# Uses vps2 (the public address): this PC cannot reach the tailnet one while
# ProtonVPN is on. Start-Process with file redirects, because PowerShell 5.1 would
# re-encode a binary tar passed through its own pipeline.

param(
  [string]$Dest = 'D:\WebGUI Trading with Schwab\Social Media',
  [string]$SshHost = 'vps2',
  [string]$RemoteRepo = '/home/administrator/dev'
)

$ErrorActionPreference = 'Stop'
$export = Join-Path $PSScriptRoot 'social_cards_export.py'
$log = Join-Path $PSScriptRoot 'pull_social_cards.log'
$tmp = Join-Path $env:TEMP ('social_cards_' + [guid]::NewGuid().ToString('N') + '.tar')
$err = $tmp + '.err'

function Write-Log([string]$msg) {
  $line = '{0:yyyy-MM-dd HH:mm:ss} {1}' -f (Get-Date), $msg
  Add-Content -Path $log -Value $line -Encoding UTF8
  Write-Output $line
}

if ((Test-Path $log) -and (Get-Item $log).Length -gt 256KB) {
  Get-Content $log -Tail 500 | Set-Content $log -Encoding UTF8
}

$code = 0
try {
  if (-not (Test-Path $export)) { throw "missing $export" }
  New-Item -ItemType Directory -Force -Path $Dest | Out-Null

  # No double quotes in the remote command: ssh joins its arguments with spaces and
  # the server's shell reads the && (PowerShell 5.1 mangles quotes for native commands).
  $sshArgs = "-o BatchMode=yes -o ConnectTimeout=30 $SshHost cd $RemoteRepo && .venv/bin/python -"
  $p = Start-Process -FilePath 'ssh.exe' -ArgumentList $sshArgs -NoNewWindow -Wait -PassThru `
         -RedirectStandardInput $export -RedirectStandardOutput $tmp -RedirectStandardError $err
  if ($p.ExitCode -ne 0) {
    $why = if (Test-Path $err) { (Get-Content $err -Raw) } else { '' }
    throw ("ssh or the export failed (exit {0}): {1}" -f $p.ExitCode, "$why".Trim())
  }

  $names = @(& tar.exe -tf $tmp)
  if ($LASTEXITCODE -ne 0 -or $names.Count -eq 0) { throw 'the tar from the server is empty or unreadable' }
  $bad = @($names | Where-Object { $_ -notmatch '^(Blog|Trade Ideas)/[^/\\:]+\.(png|jpg|txt)$' -or $_ -match '\.\.' })
  if ($bad.Count) { throw ("unexpected names in the tar: " + ($bad -join ', ')) }

  $before = @(Get-ChildItem -Path $Dest -Recurse -File).Count
  & tar.exe -xf $tmp -C $Dest
  if ($LASTEXITCODE -ne 0) { throw "unpacking failed (tar exit $LASTEXITCODE)" }
  $after = @(Get-ChildItem -Path $Dest -Recurse -File).Count
  Write-Log ("ok: {0} files on the server, {1} new here, {2} in the folder" -f $names.Count, ($after - $before), $after)
} catch {
  Write-Log ("FAILED: " + $_)
  $code = 1
} finally {
  Remove-Item -LiteralPath $tmp, $err -ErrorAction SilentlyContinue
}
exit $code
