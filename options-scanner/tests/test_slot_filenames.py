import pytest
from gamma_tool import slot_filenames
from gamma_tool import slot_tag_for_time


def test_slot_0820():
    detail, summary = slot_filenames("0820")
    assert detail == "gex_analysis_prompt_0820.txt"
    assert summary == "gex_analysis_summary_prompt_0820.txt"


def test_slot_manual():
    detail, summary = slot_filenames("manual")
    assert detail == "gex_analysis_prompt_manual.txt"
    assert summary == "gex_analysis_summary_prompt_manual.txt"


def test_slot_invalid():
    with pytest.raises(ValueError):
        slot_filenames("0830")


def test_slot_tag_for_819():
    assert slot_tag_for_time(8, 19) == "0820"

def test_slot_tag_for_844():
    assert slot_tag_for_time(8, 44) == "0845"

def test_slot_tag_for_959():
    assert slot_tag_for_time(9, 59) == "1000"

def test_slot_tag_for_1259():
    assert slot_tag_for_time(12, 59) == "1300"

def test_slot_tag_for_1459():
    assert slot_tag_for_time(14, 59) == "1500"

def test_slot_tag_unknown():
    assert slot_tag_for_time(11, 23) is None
