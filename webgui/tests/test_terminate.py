"""Tests for the Terminate page pure builder (webgui/pages/terminate.py)."""
from pages import terminate


def test_stop_command_is_detached_start_of_the_bat():
    cmd = terminate.stop_command()
    # cmd /c start <title> <bat>  → fully detached, own console.
    assert cmd[:3] == ["cmd", "/c", "start"]
    assert cmd[-1].endswith("stop_all.bat")


def test_stop_command_targets_the_repo_root_bat():
    assert terminate.STOP_BAT.name == "stop_all.bat"
    assert str(terminate.STOP_BAT) == terminate.stop_command()[-1]
