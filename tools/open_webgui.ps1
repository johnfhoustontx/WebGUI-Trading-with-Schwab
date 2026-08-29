<#
.SYNOPSIS
  Open the trading web GUI, which now runs on the Linux host.

.DESCRIPTION
  Holds an SSH tunnel to the VPS and opens a browser at the local end:

      this box 127.0.0.1:8500  ->  (ssh -L)  ->  VPS 127.0.0.1:8500   web GUI
      this box 127.0.0.1:8100  ->  (ssh -L)  ->  VPS 127.0.0.1:8100   proxy

  The replacement for _open_webgui.bat, which pointed at a local stack that no
  longer exists.

  WHY BOTH PORTS. The Schwab refresh token expires every 7 days, and re-minting
  it goes through the proxy's own paste-the-URL form at
  http://127.0.0.1:8100/auth. Forwarding only the web GUI meant that page was
  unreachable exactly when it was needed, and the failure looked like a broken
  proxy rather than a missing forward. :8100 also serves /health, which is the
  honest answer to "is Schwab auth actually working".

  WHY A TUNNEL AND NOT A URL. Both services bind 127.0.0.1 on the VPS and have
  NO AUTHENTICATION OF ANY KIND. That is correct for a desk-side app and it is
  the whole problem on a server: the web GUI can open paper positions, apply
  rescue adjustments, arm the autonomous driver and stop the entire stack, and
  the proxy holds the Schwab credentials. Exposing either publicly would hand
  that to whoever found the port. The tunnel gives you both while they stay
  bound to loopback on each end, authenticated by your SSH key.

  ⚠ Do NOT "simplify" this by changing either bind address to 0.0.0.0.

  The window stays open while you use the app -- closing it closes the tunnel.
  That is deliberate: a forgotten background tunnel is an access path nobody
  remembers granting.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\open_webgui.ps1

.EXAMPLE
  # Hold the tunnels without opening a browser:
  powershell -ExecutionPolicy Bypass -File tools\open_webgui.ps1 -NoBrowser
#>
[CmdletBinding()]
param(
  # Tailscale alias by default: it survives the VPS's public IP changing.
  # Pass -SshHost vps to force the public-IP route if Tailscale is down.
  [string]$SshHost   = "vps-ts",
  [int]$WebPort      = 8500,
  [int]$ProxyPort    = 8100,
  [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$url = "http://127.0.0.1:$WebPort/"
$authUrl = "http://127.0.0.1:$ProxyPort/auth"

# Refuse rather than collide. If something already holds one of these ports it
# may be another tunnel or a leftover local stack -- either way, silently failing
# to forward and then opening a browser onto the WRONG thing is the bad outcome.
foreach ($p in @($WebPort, $ProxyPort)) {
  if (Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue) {
    Write-Warning "127.0.0.1:$p is already in use locally."
    Write-Warning "If that is an existing tunnel, just open $url."
    Write-Warning "If it is something else, close it first -- this script will not overwrite it."
    exit 1
  }
}

Write-Output "Opening tunnels to $SshHost (web $WebPort, proxy $ProxyPort) ..."
$ssh = Start-Process ssh -PassThru -WindowStyle Hidden -ArgumentList @(
  "-N", "-T",
  "-o", "BatchMode=yes",
  # Fail loudly if either forward cannot be established, rather than sitting
  # connected with one of them silently missing.
  "-o", "ExitOnForwardFailure=yes",
  "-o", "ServerAliveInterval=30",
  "-o", "ServerAliveCountMax=3",
  "-L", "${WebPort}:127.0.0.1:${WebPort}",
  "-L", "${ProxyPort}:127.0.0.1:${ProxyPort}",
  $SshHost
)

function Test-Answers($u) {
  try {
    Invoke-WebRequest -Uri $u -TimeoutSec 3 -UseBasicParsing -MaximumRedirection 0 -ErrorAction Stop | Out-Null
    return $true
  } catch {
    # A 307 to /desk is success, not failure.
    return ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -ge 200)
  }
}

try {
  # Wait for the far end to actually ANSWER, not merely for the port to bind --
  # a forward is established before the app is ready, and a browser opened into
  # that window shows a connection error the user then has to guess about.
  $ready = $false
  foreach ($i in 1..30) {
    if ($ssh.HasExited) { throw "ssh exited (code $($ssh.ExitCode)) - is '$SshHost' in ~/.ssh/config, and is the VPS up?" }
    if (Test-Answers $url) { $ready = $true; break }
    Start-Sleep -Milliseconds 700
  }
  if (-not $ready) { throw "tunnel opened but $url did not answer" }

  Write-Output ""
  Write-Output "  Web GUI     $url"
  if (Test-Answers "http://127.0.0.1:$ProxyPort/health") {
    Write-Output "  Schwab auth $authUrl   (re-mint the 7-day refresh token here)"
  } else {
    Write-Warning "  proxy :$ProxyPort forwarded but not answering - is trading-prod-proxy running?"
  }
  Write-Output ""

  if (-not $NoBrowser) { Start-Process $url }
  Write-Output "Tunnels are open. Close this window (or Ctrl+C) to close them."
  while (-not $ssh.HasExited) { Start-Sleep -Seconds 2 }
  Write-Output "ssh exited (code $($ssh.ExitCode)); tunnels closed."
}
finally {
  if ($ssh -and -not $ssh.HasExited) {
    Write-Output "Closing tunnels..."
    Stop-Process -Id $ssh.Id -Force -ErrorAction SilentlyContinue
  }
}
