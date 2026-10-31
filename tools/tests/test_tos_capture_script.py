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
    assert body.index("Stop-Process -Id") < body.index("Release-Com")


def test_a_dead_excel_is_seen_by_its_process_id():
    assert re.search(r"Get-Process\s+-Id\s+\$", CODE)


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


def test_installer_runs_only_while_the_operator_is_logged_on_and_holds_no_credential():
    assert "-LogonType Interactive" in INSTALL
    assert "password" not in INSTALL.lower()
    assert "-Credential" not in INSTALL and "Get-Credential" not in INSTALL


def test_installer_copies_the_script_so_a_branch_change_cannot_alter_the_feed():
    assert "Copy-Item" in INSTALL and "$env:LOCALAPPDATA" in INSTALL
    assert "TosCapture" in INSTALL
