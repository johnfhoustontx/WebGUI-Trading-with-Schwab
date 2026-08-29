<#
.SYNOPSIS
  Keep prod's Schwab proxy reachable from the Linux VPS, for the parallel-run week.

.DESCRIPTION
  Holds a reverse SSH tunnel so the VPS shadow stack can borrow THIS machine's
  proxy:

      VPS 127.0.0.1:8100  ->  (ssh -R)  ->  this box 127.0.0.1:8100

  WHY A BORROWED PROXY AT ALL. The Schwab OAuth refresh token is a single
  rotating credential -- two proxies holding it invalidate each other. So the
  shadow stack runs with owns_proxy = false and has no proxy unit of its own,
  and prod stays the only holder.

  WHY THE TUNNEL RUNS FROM HERE. This box is behind home NAT, so the VPS cannot
  initiate. The connection has to be outbound, which means the supervisor has to
  live on Windows.

  WHY NOT autossh. It is a Cygwin/MSYS tool with no Windows build, and neither
  scoop nor choco is installed here. Everything autossh actually provides is
  reproduced below: ServerAliveInterval/CountMax is its connection monitoring,
  ExitOnForwardFailure makes a half-open tunnel fail loudly instead of silently,
  and the loop is its respawn. The backoff is the same storm-cap reasoning as the
  systemd units -- retry a few times, then slow down rather than hammering.

  ⚠ NOTHING IS EXPOSED PUBLICLY. -R binds the VPS's LOOPBACK only (sshd defaults
  to GatewayPorts no). The proxy is unauthenticated; it must never be reachable
  from the internet on either end.

  ⚠ TEMPORARY. DELETE THIS AT CUTOVER (plan Task 25). Once the VPS owns the
  proxy, a tunnel back to a decommissioned Windows box is worse than useless --
  it would be a second holder of the refresh token.

.EXAMPLE
  # Run in the foreground to watch it:
  powershell -ExecutionPolicy Bypass -File tools\prod_proxy_tunnel.ps1

.NOTES
  DEPLOYING. This file is the SOURCE. The RUNNING copy lives at

      %LOCALAPPDATA%\ProdProxyTunnel\prod_proxy_tunnel.ps1

  deliberately OUTSIDE every git checkout. Pointing the logon entry at a path
  inside the repo makes the tunnel a hostage of git: removing the worktree,
  switching branches, or merging at cutover would stop it SILENTLY at the next
  logon -- and a tunnel that is simply absent looks identical to one that is
  working, right up until something needs data.

  Deploy or refresh it with:

      Copy-Item tools\prod_proxy_tunnel.ps1 `
        (Join-Path $env:LOCALAPPDATA 'ProdProxyTunnel\prod_proxy_tunnel.ps1') -Force

  It is a COPY, not a link, so it can drift. Re-copy after editing here.

  LOGON PERSISTENCE uses the per-user Startup folder, not a scheduled task:
  `schtasks /SC ONLOGON` writes to the root task folder and needs elevation,
  which is why it returns "Access is denied" unelevated. Startup needs none, and
  running as the logged-on user is not a compromise here -- the tunnel needs
  that user's SSH key from ~/.ssh.

      %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\ProdProxyTunnel.vbs

  A .vbs rather than a .cmd, so there is no console flash at logon.
#>
[CmdletBinding()]
param(
  [string]$SshHost = "vps",     # the Host alias in ~/.ssh/config
  [int]$Port       = 8100,      # proxy port, both ends
  [int]$MinBackoff = 5,
  [int]$MaxBackoff = 120,
  # Deliberately NOT repo-relative. The deployed copy lives outside every git
  # checkout (see .NOTES), so a path derived from $PSScriptRoot/.. would scatter
  # logs wherever the script happened to be copied. LOCALAPPDATA is per-user,
  # always writable, and untouched by any git operation.
  [string]$LogPath = (Join-Path $env:LOCALAPPDATA "ProdProxyTunnel\prod_proxy_tunnel.log")
)

$ErrorActionPreference = "Stop"
$logDir = Split-Path -Parent $LogPath
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$log = $LogPath

function Write-Log($msg) {
  $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -Path $log -Value $line -Encoding utf8
  Write-Output $line
}

Write-Log "starting supervisor: ${SshHost} -R ${Port} (pid $PID)"

# Reap ORPHANED forwards before claiming the port.
#
# An `ssh -R` child outlives its supervisor: kill the PowerShell and the tunnel
# keeps working, unsupervised. That state is genuinely misleading -- the forward
# answers, so everything looks healthy, while nothing is left to reconnect it
# when it eventually drops. It also blocks the next supervisor, whose
# ExitOnForwardFailure correctly refuses a port someone else already holds, so a
# restart quietly fails to take over.
#
# Observed 2026-08-29 while testing the logon entry: an orphan kept answering
# after its supervisor was gone, and the "new" tunnel that appeared to come up
# was the old one all along.
#
# Scoped to this exact forward, so it cannot touch unrelated ssh sessions --
# including the one an operator may be sitting in.
$orphans = @(Get-CimInstance Win32_Process -Filter "Name='ssh.exe'" -ErrorAction SilentlyContinue |
             Where-Object { $_.CommandLine -and $_.CommandLine -match "-R\s+${Port}:127\.0\.0\.1:${Port}" -and $_.ProcessId -ne $PID })
foreach ($o in $orphans) {
  Write-Log "reaping orphaned tunnel ssh pid $($o.ProcessId)"
  try { Stop-Process -Id $o.ProcessId -Force -ErrorAction Stop } catch { Write-Log "  could not kill: $_" }
}
if ($orphans.Count) { Start-Sleep -Seconds 3 }   # let the far end release the port

$backoff = $MinBackoff

while ($true) {
  $started = Get-Date

  # -N            no remote command, forwarding only
  # -T            no pty
  # ExitOnForwardFailure  fail loudly if :$Port is already held on the VPS,
  #               rather than sitting connected with no working forward
  # ServerAlive*  autossh's job: notice a silently dead link within ~90s
  $sshArgs = @(
    "-N", "-T",
    "-o", "BatchMode=yes",
    "-o", "ExitOnForwardFailure=yes",
    "-o", "ServerAliveInterval=30",
    "-o", "ServerAliveCountMax=3",
    "-o", "StrictHostKeyChecking=accept-new",
    "-R", "${Port}:127.0.0.1:${Port}",
    $SshHost
  )

  & ssh @sshArgs 2>&1 | ForEach-Object { Write-Log "ssh: $_" }
  $code = $LASTEXITCODE
  $ranFor = [int]((Get-Date) - $started).TotalSeconds

  # A tunnel that held for a while then dropped is a network blip: retry fast.
  # One that dies immediately is a real fault (port taken, key rejected, host
  # unknown) -- backing off stops it filling the log at machine speed.
  if ($ranFor -ge 60) {
    Write-Log "tunnel held ${ranFor}s then exited ($code); reconnecting"
    $backoff = $MinBackoff
  } else {
    Write-Log "tunnel died after ${ranFor}s ($code); retrying in ${backoff}s"
    Start-Sleep -Seconds $backoff
    $backoff = [Math]::Min($backoff * 2, $MaxBackoff)
  }
}
