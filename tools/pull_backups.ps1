<#
.SYNOPSIS
  Pull the trading stack's newest backup off the VPS to local storage.

.DESCRIPTION
  The third and most important backup layer. The VPS runs tools/backup_local.py
  nightly and keeps three dated generations -- but those live on the same disk as
  the thing they protect. They cover corruption, a bad migration, and a mistaken
  delete. They do NOT cover losing the instance: a provider incident, a billing
  lapse, or a rebuild takes the backups with the data.

  This is the copy that survives that. It replaces the old E:-drive robocopy
  routine, which died with the Windows prod stack.

  WHAT IS IRREPLACEABLE, and why this is not routine hygiene:
    paper_account.db / paper_account_driver.db  the books (driver: history only)
    signals.db                                  what the model said, and when
    gex_history.db                              ~1.5 GB of intraday dealer
                                                positioning that CANNOT be
                                                re-fetched -- Schwab serves no
                                                history for it
  Losing these is not a restore-from-upstream situation. They stop existing.

  HOSTS. -SshHost is an ordered list, tried in turn: the Tailscale alias first
  (it survives an IP change), then the public-IP alias. Losing Tailscale must
  never mean losing the backup -- on 2026-09-25 the tailnet route refused TCP
  while Tailscale still listed the node as up, and only the public route worked.
  Both aliases live in ~/.ssh/config. (The default used to be `vps-ts`, the
  suspended original server, so the script could not have run at all.)

  WHAT COUNTS AS A GENERATION. Only a directory named exactly the way
  backup_local.py names one -- `<env>_YYYY-MM-DD_HHMM` -- is ever listed,
  pulled, counted or pruned. The destination holds other things (`_keys`, the
  age identity that decrypts the offsite archives), and an unscoped prune sorted
  `_keys` last and would have deleted it once more than -Keep folders existed.

  VERIFICATION happens BEFORE the rename, not after: every file the VPS has must
  land at the same size, and every SQLite database must pass `quick_check`. A
  generation that fails stays `.partial`, which nothing here counts or prunes,
  so a bad copy can never look like a backup and never displaces a good one.

.PARAMETER Fresh
  Run the VPS backup unit first (`trading-<env>-backup.service`, the same one
  the nightly timer starts) so the pull includes today. That unit also prunes
  the VPS to its own newest three and uploads offsite, as it does every night.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\pull_backups.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\pull_backups.ps1 -Fresh

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\pull_backups.ps1 -Dest 'E:\TradingBackups' -Keep 5
#>
[CmdletBinding()]
param(
  [string[]]$SshHost = @("vps2-ts", "vps2"),  # tailnet first, public IP as the fallback
  [string]$Dest      = "E:\TradingBackups",    # 3.6 TB free, and off the working drives
  [int]$Keep         = 5,
  [string]$EnvName   = "prod",                 # backup_local.py prefixes generations with ENV_NAME
  [switch]$Fresh
)

$ErrorActionPreference = "Stop"

function Say($m) { Write-Output ("[pull] " + $m) }

# Native commands run with the preference relaxed: under Windows PowerShell 5.1,
# "Stop" turns any stderr line from ssh/scp into a terminating error, so a
# harmless warning would abort the pull. Exit codes are checked explicitly.
function Invoke-Native([scriptblock]$Block) {
  $saved = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try { $out = & $Block } finally { $ErrorActionPreference = $saved }
  return , @($out)
}

$genPattern = '^' + [regex]::Escape($EnvName) + '_\d{4}-\d{2}-\d{2}_\d{4}$'
function Get-LocalGenerations {
  Get-ChildItem -LiteralPath $Dest -Directory | Where-Object { $_.Name -match $genPattern }
}

if ($Keep -lt 1) { throw "-Keep must be at least 1" }
if (-not (Test-Path -LiteralPath $Dest)) { New-Item -ItemType Directory -Path $Dest -Force | Out-Null }

# --- 1. Find a host that answers ---------------------------------------------
$sshHostUsed = $null
foreach ($h in $SshHost) {
  $null = Invoke-Native { ssh -o BatchMode=yes -o ConnectTimeout=10 $h true }
  if ($LASTEXITCODE -eq 0) { $sshHostUsed = $h; break }
  Say "$h did not answer (ssh exit $LASTEXITCODE)"
}
if (-not $sshHostUsed) { throw "no host answered: $($SshHost -join ', ')" }
Say "using $sshHostUsed"

# --- 2. Optionally take a fresh generation -----------------------------------
if ($Fresh) {
  $unit = "trading-$EnvName-backup.service"
  Say "running $unit on the VPS (about a minute)..."
  $null = Invoke-Native { ssh -o BatchMode=yes $sshHostUsed "systemctl --user start $unit" }
  if ($LASTEXITCODE -ne 0) {
    throw "$unit failed (exit $LASTEXITCODE) - see: journalctl --user -u $unit"
  }
}

# --- 3. Which generation is newest? Ask the VPS rather than guess a name. ----
$listing = Invoke-Native { ssh -o BatchMode=yes $sshHostUsed 'ls -1 ~/backups' }
if ($LASTEXITCODE -ne 0) { throw "could not list ~/backups on $sshHostUsed" }
$name = $listing | ForEach-Object { "$_".Trim() } | Where-Object { $_ -match $genPattern } |
        Sort-Object -Descending | Select-Object -First 1
if (-not $name) { throw "no '$EnvName' generation found in ~/backups on $sshHostUsed" }
Say "newest generation on $sshHostUsed : $name"

$local = Join-Path $Dest $name
if (Test-Path -LiteralPath $local) {
  Say "$name already pulled - nothing to do."
} else {
  # Into a .partial first, renamed only once verified. An interrupted or short
  # transfer that leaves a correctly-named directory is worse than no copy at
  # all: it looks like a backup and is not one.
  $partial = "$local.partial"
  if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Recurse -Force }
  New-Item -ItemType Directory -Path $partial -Force | Out-Null

  Say "pulling ~1.5 GB (this takes a few minutes)..."
  $null = Invoke-Native { scp -q -r "${sshHostUsed}:backups/$name/." $partial }
  if ($LASTEXITCODE -ne 0) { throw "scp failed (exit $LASTEXITCODE); left $partial in place for inspection" }

  # --- 4. Verify what LANDED against what the VPS holds, file by file. -------
  $manifest = Invoke-Native { ssh -o BatchMode=yes $sshHostUsed "cd ~/backups/$name && find . -type f -printf '%P|%s\n'" }
  if ($LASTEXITCODE -ne 0) { throw "could not read the remote manifest; left $partial in place" }
  $remote = @{}
  foreach ($line in $manifest) {
    $i = "$line".LastIndexOf('|')
    if ($i -gt 0) { $remote["$line".Substring(0, $i)] = [int64]"$line".Substring($i + 1) }
  }
  $landed = @{}
  Get-ChildItem -LiteralPath $partial -Recurse -File -Force | ForEach-Object {
    $landed[$_.FullName.Substring($partial.Length + 1).Replace('\', '/')] = $_.Length
  }
  $missing = @($remote.Keys | Where-Object { -not $landed.ContainsKey($_) })
  $short   = @($remote.Keys | Where-Object { $landed.ContainsKey($_) -and $landed[$_] -ne $remote[$_] })
  Say ("files: $($remote.Count) on the VPS, $($landed.Count) landed, " +
       "$($missing.Count) missing, $($short.Count) size mismatch")
  if ($remote.Count -eq 0 -or $missing.Count -or $short.Count) {
    ($missing + $short) | Select-Object -First 10 | ForEach-Object { Write-Warning "  $_" }
    throw "transfer incomplete; left $partial in place for inspection"
  }

  # --- 5. Every database must open and pass quick_check. ---------------------
  $py = Get-Command python -ErrorAction SilentlyContinue
  $dbs = @(Get-ChildItem -LiteralPath $partial -Recurse -File -Filter *.db)
  if ($py) {
    $check = "import sqlite3,sys; c=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True); print(c.execute('pragma quick_check').fetchone()[0])"
    $bad = @()
    foreach ($db in $dbs) {
      $r = (Invoke-Native { python -c $check $db.FullName }) -join ' '
      if ($LASTEXITCODE -ne 0 -or $r.Trim() -ne 'ok') { $bad += "$($db.Name): $r" }
    }
    if ($bad.Count) {
      $bad | ForEach-Object { Write-Warning "  $_" }
      throw "$($bad.Count) database(s) failed quick_check; left $partial in place"
    }
    Say "quick_check ok on all $($dbs.Count) databases"
  } else {
    Write-Warning "python not on PATH - database quick_check SKIPPED (sizes still verified)"
  }

  Rename-Item -LiteralPath $partial -NewName $name
  Say "pulled and verified: $local"
}

$dbs = @(Get-ChildItem -LiteralPath $local -Recurse -File -Filter *.db)
$gex = Join-Path $local 'options-scanner\gex_history.db'
Say ("databases: " + $dbs.Count + ", total " + [math]::Round((($dbs | Measure-Object Length -Sum).Sum)/1GB, 2) + " GB")
if (Test-Path -LiteralPath $gex) {
  Say ("gex_history.db: " + [math]::Round((Get-Item -LiteralPath $gex).Length/1GB, 2) + " GB")
} else {
  Write-Warning "gex_history.db MISSING from the pulled generation"
}
if ($dbs.Count -lt 15) { Write-Warning "only $($dbs.Count) databases - expected ~21." }

# --- 6. Prune: generations only, newest $Keep survive. -----------------------
# Get-LocalGenerations matches backup_local.py's exact naming, so `_keys`, a
# `.partial`, another environment's generations or any stray folder can never
# be counted or deleted here.
$gens = @(Get-LocalGenerations | Sort-Object Name -Descending)
if ($gens.Count -gt $Keep) {
  $gens | Select-Object -Skip $Keep | ForEach-Object {
    Say ("pruning " + $_.Name)
    Remove-Item -LiteralPath $_.FullName -Recurse -Force
  }
}
Say ("generations kept locally: " + @(Get-LocalGenerations).Count)
