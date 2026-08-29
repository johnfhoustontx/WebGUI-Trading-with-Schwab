<#
.SYNOPSIS
  Pull the trading stack's backups off the VPS to local storage, over Tailscale.

.DESCRIPTION
  The third and most important backup layer. The VPS runs tools/backup_local.py
  nightly and keeps three dated generations -- but those live on the same disk as
  the thing they protect. They cover corruption, a bad migration, and a mistaken
  delete. They do NOT cover losing the instance: a provider incident, a billing
  lapse, or a rebuild takes the backups with the data.

  This is the copy that survives that. It replaces the old E:-drive robocopy
  routine, which died with the Windows prod stack.

  WHAT IS IRREPLACEABLE, and why this is not routine hygiene:
    paper_account.db / paper_account_driver.db  the books
    signals.db                                  what the model said, and when
    gex_history.db                              ~1.5 GB of intraday dealer
                                                positioning that CANNOT be
                                                re-fetched -- Schwab serves no
                                                history for it
  Losing these is not a restore-from-upstream situation. They stop existing.

  Runs over the Tailscale name, so it does not depend on the VPS's public IP.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\pull_backups.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\pull_backups.ps1 -Dest 'E:\TradingBackups' -Keep 5
#>
[CmdletBinding()]
param(
  [string]$SshHost = "vps-ts",              # Tailscale alias; survives an IP change
  [string]$Dest    = "E:\TradingBackups",   # 3.6 TB free, and off the working drives
  [int]$Keep       = 5
)

$ErrorActionPreference = "Stop"

function Say($m) { Write-Output ("[pull] " + $m) }

if (-not (Test-Path $Dest)) { New-Item -ItemType Directory -Path $Dest -Force | Out-Null }

# Which generation is newest on the VPS? Ask it rather than guessing a name.
$gen = (& ssh -o BatchMode=yes $SshHost 'ls -1d ~/backups/*/ 2>/dev/null | tail -1' 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $gen) { throw "could not list backups on $SshHost (is Tailscale up?)" }
$name = Split-Path $gen.TrimEnd('/') -Leaf
Say "newest generation on $SshHost : $name"

$local = Join-Path $Dest $name
if (Test-Path $local) {
  Say "$name already pulled - nothing to do."
} else {
  # Into a .partial first, renamed only on success. An interrupted transfer that
  # leaves a correctly-named directory is worse than no copy at all: it looks
  # like a backup and is not one.
  $partial = "$local.partial"
  if (Test-Path $partial) { Remove-Item $partial -Recurse -Force }
  New-Item -ItemType Directory -Path $partial -Force | Out-Null

  Say "pulling ~1.5 GB (this takes a few minutes)..."
  & scp -q -r "${SshHost}:$($gen.TrimEnd('/'))/." $partial
  if ($LASTEXITCODE -ne 0) { throw "scp failed (exit $LASTEXITCODE); left $partial in place for inspection" }

  Rename-Item $partial $local
  Say "pulled to $local"
}

# Verify what LANDED, not what was sent. A backup nobody has checked is a
# hypothesis, and a truncated scp still produces files.
$dbs = Get-ChildItem $local -Recurse -Filter *.db
$gex = Join-Path $local 'options-scanner\gex_history.db'
Say ("databases: " + $dbs.Count + ", total " + [math]::Round((($dbs | Measure-Object Length -Sum).Sum)/1GB, 2) + " GB")
if (Test-Path $gex) {
  Say ("gex_history.db: " + [math]::Round((Get-Item $gex).Length/1GB, 2) + " GB")
} else {
  Write-Warning "gex_history.db MISSING from the pulled generation"
}
if ($dbs.Count -lt 15) { Write-Warning "only $($dbs.Count) databases - expected ~21. Transfer may be incomplete." }

# Prune old local generations. Newest $Keep survive.
$gens = Get-ChildItem $Dest -Directory | Where-Object { $_.Name -notlike '*.partial' } | Sort-Object Name -Descending
if ($gens.Count -gt $Keep) {
  $gens | Select-Object -Skip $Keep | ForEach-Object {
    Say ("pruning " + $_.Name)
    Remove-Item $_.FullName -Recurse -Force
  }
}
Say ("generations kept locally: " + (Get-ChildItem $Dest -Directory | Where-Object { $_.Name -notlike '*.partial' }).Count)
