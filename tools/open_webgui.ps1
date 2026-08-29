<#
.SYNOPSIS
  Open the trading web GUI, which now runs on the Linux host.

.DESCRIPTION
  Holds an SSH tunnel to the VPS and opens a browser at the local end:

      this box 127.0.0.1:8500  ->  (ssh -L)  ->  VPS 127.0.0.1:8500

  The replacement for _open_webgui.bat, which pointed at a local stack that no
  longer exists.

  WHY A TUNNEL AND NOT A URL. The web GUI binds 127.0.0.1 on the VPS and has NO
  AUTHENTICATION OF ANY KIND. That is correct for a desk-side app and it is the
  whole problem on a server: the UI can open paper positions, apply rescue
  adjustments, arm the autonomous driver and stop the entire stack. Exposing
  :8500 publicly would hand all of that to anyone who found the port. The tunnel
  gives you the app while it stays bound to loopback on both ends, authenticated
  by your SSH key.

  ⚠ Do NOT "simplify" this by changing the bind address to 0.0.0.0.

  The window stays open while you use the app -- closing it closes the tunnel.
  That is deliberate: a forgotten background tunnel is an access path nobody
  remembers granting.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\open_webgui.ps1

.EXAMPLE
  # No browser, just hold the tunnel (e.g. you already have a tab open):
  powershell -ExecutionPolicy Bypass -File tools\open_webgui.ps1 -NoBrowser
#>
[CmdletBinding()]
param(
  [string]$SshHost   = "vps",   # the Host alias in ~/.ssh/config
  [int]$Port         = 8500,    # web GUI port, both ends
  [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$url = "http://127.0.0.1:$Port/"

# Refuse rather than collide. If something already holds the port locally it may
# be another tunnel, or a leftover local stack -- either way, silently failing to
# forward and then opening a browser onto the WRONG thing is the bad outcome.
$inUse = $null -ne (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
if ($inUse) {
  Write-Warning "127.0.0.1:$Port is already in use locally."
  Write-Warning "If that is an existing tunnel, just open $url."
  Write-Warning "If it is something else, close it first -- this script will not overwrite it."
  exit 1
}

Write-Output "Opening tunnel to $SshHost ..."
$ssh = Start-Process ssh -PassThru -WindowStyle Hidden -ArgumentList @(
  "-N", "-T",
  "-o", "BatchMode=yes",
  "-o", "ExitOnForwardFailure=yes",
  "-o", "ServerAliveInterval=30",
  "-o", "ServerAliveCountMax=3",
  "-L", "${Port}:127.0.0.1:${Port}",
  $SshHost
)

try {
  # Wait for the far end to actually ANSWER, not merely for the port to bind --
  # a forward can be established before the app is ready, and a browser opened
  # into that window shows a connection error the user then has to guess about.
  $ready = $false
  foreach ($i in 1..30) {
    if ($ssh.HasExited) { throw "ssh exited (code $($ssh.ExitCode)) - is '$SshHost' in ~/.ssh/config, and is the VPS up?" }
    try {
      Invoke-WebRequest -Uri $url -TimeoutSec 3 -UseBasicParsing -MaximumRedirection 0 -ErrorAction Stop | Out-Null
      $ready = $true; break
    } catch {
      # A 307 to /desk is success, not failure.
      if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -ge 200) { $ready = $true; break }
    }
    Start-Sleep -Milliseconds 700
  }
  if (-not $ready) { throw "tunnel opened but $url did not answer" }

  Write-Output "Ready: $url"
  if (-not $NoBrowser) { Start-Process $url }
  Write-Output "Tunnel is open. Close this window (or Ctrl+C) to close it."
  while (-not $ssh.HasExited) { Start-Sleep -Seconds 2 }
  Write-Output "ssh exited (code $($ssh.ExitCode)); tunnel closed."
}
finally {
  if ($ssh -and -not $ssh.HasExited) {
    Write-Output "Closing tunnel..."
    Stop-Process -Id $ssh.Id -Force -ErrorAction SilentlyContinue
  }
}
