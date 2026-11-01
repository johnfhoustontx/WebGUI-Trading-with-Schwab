"""The /ES and /NQ capture on the PC, pinned at source level.

The suite runs on Linux CI with no PowerShell or Excel, so these read the script's
text, as ``test_proxy.py`` does. What they cannot prove (that it reads a chain) is
the by-hand run in the task's report.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = (ROOT / "tools" / "tos_capture" / "tos_capture.ps1").read_text(encoding="utf-8")
INSTALL = (ROOT / "tools" / "tos_capture" / "install_tos_capture.ps1").read_text(encoding="utf-8")

# The script with its comment lines removed: a rule about what the code does must
# not pass or fail on what a comment says.
CODE = "\n".join(line for line in SRC.splitlines() if not line.lstrip().startswith("#"))


def test_starts_its_own_excel_and_stops_it_by_process_id():
    assert "/automation -Embedding" in SRC and re.search(r"Stop-Process\s+-Id\s+\$", SRC)


def test_never_stops_excel_by_name():
    assert not re.search(r"Stop-Process\s+-Name|taskkill", SRC, re.I)


def test_never_enters_anything_or_starts_thinkorswim():
    for banned in ("SendKeys", "password", 'Start-Process "thinkorswim', "PlaceOrder"):
        assert banned.lower() not in SRC.lower()
    assert not re.search(r"Start-Process[^\n]*thinkorswim", SRC, re.I)


def test_ssh_is_unattended_with_no_inner_quotes():
    assert "BatchMode=yes" in SRC and "ConnectTimeout" in SRC
    assert "futures_chain_remote.sh" in SRC
    arguments = re.search(r"\$SshArguments\s*=\s*'([^']*)'", SRC)
    assert arguments, "the ssh command line is one named, single-quoted string"
    assert '"' not in arguments.group(1)


def test_reads_the_terms_off_the_contract():
    assert "DESCRIPTION" in SRC and "AM Settled" in SRC


def test_has_no_hard_coded_interval_or_series():
    assert not re.search(r"\b300\b", SRC) and "E1A" not in SRC and "interval_sec" in SRC


def test_ascii_only_for_powershell_5():
    assert SRC.isascii()
    assert INSTALL.isascii()


# ── the private Excel ────────────────────────────────────────────────────────

def test_never_attaches_to_a_running_excel():
    # GetActiveObject hands back whichever Excel registered first: the operator's.
    assert "GetActiveObject" not in SRC


def test_checks_the_excel_it_got_is_the_one_it_started():
    assert "GetWindowThreadProcessId" in CODE and ".Hwnd" in CODE


def test_the_hidden_excel_never_prompts():
    assert re.search(r"\.DisplayAlerts\s*=\s*\$false", CODE)
    assert re.search(r"\.Visible\s*=\s*\$false", CODE)


def test_excel_is_stopped_in_a_finally_and_its_com_objects_released():
    assert re.search(r"finally\s*\{[^}]*Stop-CaptureExcel", CODE)
    assert "ReleaseComObject" in CODE


def test_excel_is_stopped_first_and_never_asked_to_quit():
    # Quit() crashed this Excel once, and Windows restarted the crashed copy as a
    # visible window on the operator's desktop. Stopping the process is not a crash.
    assert ".Quit(" not in CODE
    body = CODE[CODE.index("function Stop-CaptureExcel"):CODE.index("function Test-ExcelAlive")]
    assert body.index("Stop-OwnedExcel") < body.index("Release-Com")


def test_a_dead_excel_is_seen_by_its_held_process_object():
    assert re.search(r"Get-Process\s+-Id\s+\$", CODE)
    assert ".Process.HasExited" in CODE


def _function(name):
    start = CODE.index(f"function {name} ")
    following = re.compile(r"^function ", re.M).search(CODE, start + 1)
    return CODE[start:following.start() if following else len(CODE)]


def test_the_private_copy_refuses_the_operators_files_before_any_workbook():
    # A double-clicked file is handed to a running Excel, and a hidden copy can be
    # that Excel: the operator's workbook would then open out of sight and die with it.
    body = _function("Start-CaptureExcel")
    assert re.search(r"\.IgnoreRemoteRequests\s*=\s*\$true", body)
    assert body.index("IgnoreRemoteRequests") < body.index("Workbooks.Add()")


def test_the_id_cannot_be_reused_while_the_copy_lives():
    # Reading .Handle makes the Process object hold the handle open, and an id with
    # an open handle is not given to another process.
    assert ".Handle" in _function("New-OwnedRecord") and "$script:Owned" in CODE


def test_every_excel_stop_goes_through_the_identity_check():
    body = _function("Stop-OwnedExcel")
    assert "StartTime" in body and "CommandLine" in body
    assert body.index("StartTime") < body.index("Stop-Process -Id")
    assert "left alone" in body
    for name in ("Stop-CaptureExcel", "Start-CaptureExcel", "Stop-LeftoverExcel"):
        assert "Stop-Process" not in _function(name), name
    # Only the checked stop and ssh's own, whose object is held from its start.
    assert len(re.findall(r"Stop-Process\s+-Id", CODE)) == 2


def test_a_copy_is_adopted_only_if_it_is_empty_and_hidden():
    body = _function("Start-CaptureExcel")
    assert ".Workbooks.Count -ne 0" in body and "$attached.Visible" in body
    assert body.index("Workbooks.Count") < body.index("$script:Excel = $attached")


def test_the_leftover_check_compares_the_recorded_start_time():
    assert "StartTime" in _function("Write-ExcelIdFile")
    assert "Stop-OwnedExcel" in _function("Stop-LeftoverExcel")


def test_an_unsettled_ladder_is_not_read_on_the_boundary_it_was_built_on():
    assert "Settled = $settled" in _function("Build-Ladder")
    body = _function("Read-Snapshot")
    assert "$Ladder.Settled" in body and "$Ladder.BuiltSlot" in body
    assert body.index("$Ladder.Settled") < body.index("Read-Block")


def test_there_is_a_floor_against_contracts_tried_and_it_rewrites():
    body = _function("Read-Snapshot")
    assert "$MinReadFraction" in body and "$tried" in body
    floor = body[body.index("$MinReadFraction"):]
    assert "Ladders.Remove($root)" in floor[:floor.index("return $null")]


def test_a_row_of_nothing_but_zeros_and_absences_is_left_out():
    assert "Test-RowReadsNothing" in _function("Read-Snapshot")
    assert "-ne 0" in _function("Test-RowReadsNothing")


def test_a_fall_against_the_most_read_rewrites_the_ladder():
    body = _function("Read-Snapshot")
    branch = body[body.index("if ($contracts.Count -lt $least)"):]
    assert "Ladders.Remove($root)" in branch[:branch.index("return $null")]


# ── the watchdog ─────────────────────────────────────────────────────────────

def test_a_watchdog_on_its_own_thread_stops_a_hung_excel():
    assert "[powershell]::Create()" in CODE
    assert "HangLimitSec" in CODE and "hang_limit_sec" in CODE
    assert "Stop-OwnedExcel" in CODE[CODE.index("$WatchdogBody"):]
    body = _function("Stop-Watchdog")
    assert ".Stop()" in body and ".Dispose()" in body


def test_the_heartbeat_is_kept_through_a_sleep_and_a_fill():
    assert "Update-Heartbeat" in _function("Wait-Sleeping")
    assert "Update-Heartbeat" in _function("Wait-Filled")
    assert "Wait-Sleeping" in _function("Wait-NextBoundary")
    assert "Start-Sleep" not in _function("Wait-NextBoundary")
    assert "Update-Heartbeat" in _function("Invoke-Main")


def test_the_limit_can_never_be_below_a_whole_ssh():
    assert "$SshTimeoutSec" in _function("Update-WatchdogLimit")


# ── what the plan may not carry yet ──────────────────────────────────────────

@pytest.mark.parametrize("key", [
    "fill_settle_sec", "fill_wait_max_sec", "probe_round_steps", "probe_reach",
    "recentre_share", "ssh_timeout_sec", "no_plan_retry_sec", "plan_retry_max_sec",
    "min_read_fraction", "hang_limit_sec",
])
def test_each_tunable_is_read_from_the_plan_with_a_stated_default(key):
    assert f"{key} = " in CODE[CODE.index("$PlanSettings"):CODE.index("$RtdFields")]


def test_a_plan_from_an_older_server_is_logged_once():
    body = _function("Update-PlanSettings")
    assert "$script:DefaultedSettings" in body and "Write-Log" in body


def test_the_shipped_values_and_the_scripts_defaults_agree():
    import tomllib
    with open(ROOT / "config" / "futures_chain.toml", "rb") as fh:
        shipped = tomllib.load(fh)["capture"]
    table = CODE[CODE.index("$PlanSettings"):CODE.index("$RtdFields")]
    found = re.findall(r"(\w+) = '\w+'", table)
    assert len(found) == 13
    for key in found:
        default = re.search(rf"^\$\w+ = ([\d.]+)\s*# {key}$", CODE, re.M)
        assert default, key
        # A plan with no fallback is an older server's, which has no fallback either.
        want = 0 if key == "fallback_interval_sec" else shipped[key]
        assert float(default.group(1)) == want, key


def _plan_settings():
    table = CODE[CODE.index("$PlanSettings = "):CODE.index("$RtdFields")]
    return dict(re.findall(r"(\w+) = '(\w+)'", table))


def test_each_plan_key_maps_to_the_variable_that_holds_its_default():
    """A key mapped to the wrong variable sets the wrong setting, silently: the stated
    default on the variable's own line names the same key, and the variable is used."""
    settings = _plan_settings()
    assert len(set(settings.values())) == len(settings)
    for key, variable in settings.items():
        assert re.search(rf"^\${variable} = [\d.]+\s*# {key}$", CODE, re.M), key
        uses = re.findall(rf"\${variable}\b", CODE)
        assert len(uses) >= 2, f"${variable} is set and never read"


def test_the_plan_carries_exactly_the_keys_the_script_reads():
    from shared import futures_chain
    assert set(_plan_settings()) == set(futures_chain._PC_SETTINGS)


# ── the watchdog's second stage ──────────────────────────────────────────────

def _watchdog_body():
    return SRC[SRC.index("$WatchdogBody = @'"):SRC.index("function Update-Heartbeat")]


def test_a_call_that_does_not_throw_after_the_stop_ends_the_process():
    # Stopping Excel only helps if the stuck COM call then throws. If it does not, the
    # loop is gone for good and a hung process has not failed: only an exit brings the
    # task's repeating trigger back.
    body = _watchdog_body()
    assert "$Watch.ExitAfterSec" in body
    assert "GetCurrentProcess().Kill()" in body
    assert body.index("Stop-OwnedExcel") < body.index("GetCurrentProcess().Kill()")


def test_the_exit_is_logged_before_it_happens():
    body = _watchdog_body()
    assert re.search(r"AppendAllText\(\$Watch\.LogPath", body)
    kill = body.index("GetCurrentProcess().Kill()")
    assert "AppendAllText" in body[body.rindex("ExitAfterSec", 0, kill):kill]


def test_the_exit_needs_a_heartbeat_missing_since_the_stop():
    # A loop that has beaten since the stop came back by itself and must be left alone.
    body = _watchdog_body()
    assert "$Watch.Beat -gt $stoppedAt" in body or "$Watch.Beat -lt $stoppedAt" in body


def test_the_exit_time_is_the_plans():
    assert "$script:Watch.ExitAfterSec" in _function("Update-WatchdogLimit")
    assert "$HangExitAfterSec" in _function("Update-WatchdogLimit")
    assert _plan_settings()["hang_exit_after_sec"] == "HangExitAfterSec"


def test_the_reasons_are_joined_as_the_server_parses_them():
    # The server splits on ";" and a space before a root's name, and each sentence can
    # hold a semicolon of its own. With a bare space, /ES's label carries /NQ's reason.
    assert "$reasons -join '; '" in CODE


# ── the rest ─────────────────────────────────────────────────────────────────

def test_plan_failures_back_off():
    body = _function("Update-Plan")
    assert "$script:PlanRetryAt" in body and "$PlanRetryMaxSec" in body
    assert "$script:PlanRetryDelay * 2" in body


def test_thinkorswim_is_picked_by_a_stable_rule():
    assert "Sort-Object StartTime, Id" in _function("Invoke-Prepare")
    assert "$thinkorswim[0]" not in CODE


def test_a_failed_prepare_at_the_boundary_still_reports():
    loop = _function("Invoke-Main")
    keep = loop[loop.index("Invoke-Prepare -KeepTime"):]
    assert "$script:GeneralProblem" in keep[:keep.index("Invoke-Capture")]


def test_a_restored_window_is_logged_and_left_alone():
    body = _function("Write-RestoreNotice")
    assert "/restore" in body and "Write-Log" in body and "Stop-" not in body
    assert "Write-RestoreNotice" in _function("Invoke-Prepare")


@pytest.mark.parametrize("name", [
    "fake_ssh.py", "check_functions.ps1", "check_live.ps1", "check_watchdog.ps1",
])
def test_the_by_hand_checks_are_kept(name):
    path = ROOT / "tools" / "tos_capture" / "by_hand_checks" / name
    assert path.read_text(encoding="utf-8").isascii()


def test_formulas_go_in_as_one_bulk_assignment():
    assert "New-Object 'object[,]'" in CODE and re.search(r"\.Formula\s*=", CODE)
    assert '=RTD("tos.rtd",,' in CODE


# ── reading ThinkorSwim ──────────────────────────────────────────────────────

def test_thinkorswim_is_only_looked_at():
    assert re.search(r"Get-Process\s+-Name\s+'?thinkorswim", CODE)


def test_gamma_and_the_late_expiry_field_are_never_read():
    assert "GAMMA" not in CODE and "EXPIRATION_DAY" not in CODE


def test_the_date_cannot_match_inside_a_longer_date():
    # "2 NOV 26" is inside "12 NOV 26": the plan's date is matched on its edges.
    assert "[regex]::Escape($plan.date_text)" in CODE or "[regex]::Escape($DateText)" in CODE
    assert r"(^|\s)" in CODE


def test_a_volatility_is_never_rescaled():
    # The wire is the percent ThinkorSwim prints. Dividing or multiplying by 100
    # anywhere in the volatility's path would misstate every gamma by that much.
    body = re.search(r"function ConvertTo-PercentReading.*?^\}", CODE, re.S | re.M)
    assert body, "the volatility has one reader"
    assert "100" not in body.group(0) and "/" not in body.group(0) and "*" not in body.group(0)
    assert "EndsWith('%')" in body.group(0)


def test_numbers_are_parsed_the_same_on_every_locale():
    assert "InvariantCulture" in CODE


def test_a_half_filled_ladder_is_waited_for():
    assert "function Wait-Filled" in CODE
    assert "$FillSettleSec" in CODE and "$FillWaitMaxSec" in CODE
    assert "min_fill_fraction" in CODE


# ── the plan and the clock ───────────────────────────────────────────────────

@pytest.mark.parametrize("field", [
    "valid_until", "date_text", "candidates", "strike_step", "strike_width_pct",
    "interval_sec", "exchange", "opens", "expiry", "min_fill_fraction", "max_snapshot_kb",
])
def test_every_value_comes_from_the_plan(field):
    assert field in CODE


def test_captured_at_is_an_instant_not_the_pcs_local_clock():
    assert "ToUnixTimeMilliseconds" in CODE
    assert "captured_at" in CODE
    assert "Get-Date -UFormat" not in CODE


def test_the_boundaries_follow_the_cadence_not_the_plan_alone():
    assert "$interval = Get-CaptureInterval" in _function("Wait-NextBoundary")


def test_a_fallback_at_or_below_the_interval_is_none():
    assert "[double]$FallbackIntervalSec -gt $interval" in _function("Get-CaptureInterval")


def test_every_kind_of_miss_moves_the_cadence():
    capture = _function("Invoke-Capture")
    assert "Update-Cadence $false $reason" in capture
    assert "$script:SendFailed" in capture
    assert "$took -ge [double]$plan.interval_sec" in capture
    assert "Update-Cadence $true" in capture
    assert "Update-Cadence $false $_.Exception.Message" in _function("Invoke-Main")
    # Every way a send can fail says so, and the flag is cleared first.
    assert _function("Send-Body").count("$script:SendFailed = $true") == 3
    assert "$script:SendFailed = $false" in _function("Send-Body")


def test_each_session_starts_at_the_plans_interval():
    capture = _function("Invoke-Capture")
    assert "$script:CleanOwed = 0" in capture[:capture.index("$began")]


def test_a_miss_owes_every_clean_capture_again():
    assert "$script:CleanOwed = [int]$FallbackCleanCaptures" in _function("Update-Cadence")


def test_the_boundaries_are_measured_from_the_epoch():
    assert re.search(r"\[math\]::Floor\(\$now\s*/\s*\$interval\)", CODE, re.I)


# ── the wire ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field", [
    "instrument", "expiry", "captured_at", "descriptions", "underlying_symbol",
    "underlying_price", "contracts", "series", "side", "strike", "mark", "bid", "ask",
    "volume", "open_int", "iv", "delta",
])
def test_sends_exactly_the_wire_fields(field):
    assert re.search(rf"\b{field}\s*=", CODE), field


def test_the_ingest_body_has_snapshots_and_a_status_with_a_reason():
    for field in ("snapshots", "status", "reason", "at"):
        assert re.search(rf"\b{field}\s*=", CODE), field


def test_stdin_is_utf8_with_no_byte_order_mark():
    assert "UTF8Encoding($false)" in CODE
    # Bytes straight to the pipe: .NET Framework's StandardInput writer takes the
    # console's encoding, and can put a mark on the front.
    assert "StandardInput.BaseStream" in CODE


def test_a_hung_ssh_cannot_stop_the_loop():
    assert "WaitForExit($timeoutMs)" in CODE
    assert re.search(r"Stop-Process\s+-Id\s+\$\w*[sS]sh", CODE) or re.search(
        r"Stop-Process\s+-Id\s+\$process\.Id", CODE)
    # Both drained at once, and the write is itself bounded: a pipe nobody reads
    # blocks a plain Write for ever.
    assert "StandardOutput.ReadToEndAsync" in CODE and "StandardError.ReadToEndAsync" in CODE
    assert "WriteAsync" in CODE


def test_ssh_goes_through_a_redirected_process():
    assert "System.Diagnostics.ProcessStartInfo" in CODE
    assert "RedirectStandardInput = $true" in CODE
    assert "UseShellExecute = $false" in CODE


def test_nothing_is_run_from_text_or_opened_to_the_network():
    assert "Invoke-Expression" not in SRC and not re.search(r"\biex\b", SRC, re.I)
    assert "0.0.0.0" not in SRC


# ── the dated workbook ───────────────────────────────────────────────────────

def test_the_workbook_is_named_for_the_expiry_under_documents():
    assert "ToS 0DTE" in CODE and "$env:USERPROFILE" in CODE and ".xlsx" in CODE


def test_a_held_workbook_is_skipped_and_never_overwritten():
    # Opened exclusively first: a file the operator holds fails here and is logged.
    assert re.search(r"\[IO\.File\]::Open\([^)]*'None'\)", CODE)
    assert ".ReadOnly" in CODE


def test_the_send_comes_before_the_workbook_and_survives_it():
    send = CODE.index("Send-Body $body", CODE.index("function Invoke-Capture"))
    append = CODE.index("Add-ToWorkbook", CODE.index("function Invoke-Capture"))
    assert send < append


def test_a_second_copy_does_not_run():
    assert "Threading.Mutex" in CODE


# ── the switches and the log ─────────────────────────────────────────────────

@pytest.mark.parametrize("switch", ["$Once", "$NoSend", "$OutFile", "$PlanFile"])
def test_has_the_switches(switch):
    head = CODE[CODE.index("param("):CODE.index(")", CODE.index("param(") + 200)]
    assert switch in head


def test_logs_where_the_operator_looks():
    assert "$env:LOCALAPPDATA" in CODE and "TosCapture" in CODE and "capture.log" in CODE


# ── the installer ────────────────────────────────────────────────────────────

def test_installer_registers_the_named_task_at_logon():
    assert "ToS 0DTE Capture" in INSTALL
    assert "Register-ScheduledTask" in INSTALL and "-AtLogOn" in INSTALL


def test_installer_runs_one_copy_restarts_it_and_keeps_it_hidden():
    assert "-MultipleInstances IgnoreNew" in INSTALL
    assert "-RestartCount" in INSTALL and "-RestartInterval" in INSTALL
    assert "-Hidden" in INSTALL


def test_installer_starts_the_capture_with_no_window_anyone_can_close():
    # A hidden-window flag on powershell.exe is not enough: where Windows Terminal is the
    # default terminal, it takes the console over and shows a window of its own.
    assert "-Execute 'conhost.exe'" in INSTALL
    assert "'--headless powershell.exe " in INSTALL
    assert "-WindowStyle" not in INSTALL


def test_capture_stops_when_the_task_ends_its_console_host():
    # Stop-ScheduledTask ends only the headless host. Left running, this copy would hold
    # the mutex and keep the old code running after a stop and a start.
    main = CODE[CODE.index("function Invoke-Main"):]
    assert "$script:ConsoleHost = Get-ConsoleHost" in main
    boundary = main.index("Wait-NextBoundary")
    gone = main.index("if (Test-HostGone)", boundary)
    assert gone < main.index("Invoke-Prepare -KeepTime")
    assert "return" in main[gone:main.index("Invoke-Prepare -KeepTime")]


def test_a_sleep_ends_early_when_the_console_host_has_gone():
    sleeping = CODE[CODE.index("function Wait-Sleeping"):CODE.index("function Wait-NextBoundary")]
    assert "Test-HostGone" in sleeping
    boundary = CODE[CODE.index("function Wait-NextBoundary"):CODE.index("function Test-RowReadsNothing")]
    assert "Test-HostGone" in boundary


def test_only_a_console_host_parent_is_watched():
    # Run by hand from a shell, or by an older task, the parent is no console host, and
    # that parent ending must not stop a capture. -Once never reaches the loop.
    body = CODE[CODE.index("function Get-ConsoleHost"):CODE.index("function Test-HostGone")]
    assert "ProcessName -eq 'conhost'" in body
    # Holding the handle keeps HasExited true to THIS process if its id is reused.
    assert ".Handle" in body


def test_installer_runs_only_while_the_operator_is_logged_on_and_holds_no_credential():
    assert "-LogonType Interactive" in INSTALL
    assert "password" not in INSTALL.lower()
    assert "-Credential" not in INSTALL and "Get-Credential" not in INSTALL


def test_installer_brings_an_exited_capture_back():
    assert "-RepetitionInterval" in INSTALL and ".Repetition" in INSTALL


def test_installer_says_a_rerun_leaves_the_running_copy_on_old_code():
    assert "next logon" in INSTALL and "old code" in INSTALL


def test_installer_copies_the_script_so_a_branch_change_cannot_alter_the_feed():
    assert "Copy-Item" in INSTALL and "$env:LOCALAPPDATA" in INSTALL
    assert "TosCapture" in INSTALL
