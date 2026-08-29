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

.EXAMPLE
  # Survive logon/reboot (run once, from the repo root):
  schtasks /Create /TN "ProdProxyTunnel" /SC ONLOGON /RL LIMITED /F `
    /TR "powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$PWD\tools\prod_proxy_tunnel.ps1`""
#>
[CmdletBinding()]
param(
  [string]$SshHost = "vps",     # the Host alias in ~/.ssh/config
  [int]$Port       = 8100,      # proxy port, both ends
  [int]$MinBackoff = 5,
  [int]$MaxBackoff = 120
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $root "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir "prod_proxy_tunnel.log"

function Write-Log($msg) {
  $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -Path $log -Value $line -Encoding utf8
  Write-Output $line
}

Write-Log "starting supervisor: ${SshHost} -R ${Port} (pid $PID)"
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
