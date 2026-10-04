"""CLAUDE.md stays the size its own rules say it should be (audit CQ-09).

The file is loaded in full at the start of every session, so its length is a
cost every session pays. It has been cut three times (2026-08-07, 2026-08-16,
2026-10-04) and refilled twice, in nine days the first time, because nothing
failed when it grew. This does.

The detail it used to carry lives in ``docs/reference/``: each file there holds
the incidents, measurements and reasoning behind the rules CLAUDE.md keeps.
"""
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]
CLAUDE = REPO / "CLAUDE.md"
REFERENCE = REPO / "docs" / "reference"

# The file's own stated target. Counted with LF line endings, so the number does
# not depend on how a checkout stores them. Lower it when the file shrinks; do
# not raise it: move the detail to docs/reference/ and keep the rule.
CEILING_BYTES = 100 * 1024


def _text():
    return CLAUDE.read_bytes().decode("utf-8").replace("\r\n", "\n")


def test_claude_md_is_under_its_own_target():
    size = len(_text().encode("utf-8"))
    assert size <= CEILING_BYTES, (
        f"CLAUDE.md is {size / 1024:.1f} KB; its ceiling is "
        f"{CEILING_BYTES / 1024:.0f} KB. Keep the RULE here in a line or two and "
        "move the detail (the incident, the measurement, the reasoning) to the "
        "matching file under docs/reference/.")


def test_every_reference_file_it_links_exists():
    linked = set(re.findall(r"\]\(docs/reference/([\w.-]+\.md)\)", _text()))
    assert linked, "CLAUDE.md links no reference file"
    missing = sorted(name for name in linked if not (REFERENCE / name).is_file())
    assert not missing, f"CLAUDE.md links reference files that do not exist: {missing}"


def test_every_reference_file_is_linked_from_claude_md():
    # A reference file nothing points at is detail no session will find.
    linked = set(re.findall(r"\]\(docs/reference/([\w.-]+\.md)\)", _text()))
    orphans = sorted(p.name for p in REFERENCE.glob("*.md") if p.name not in linked)
    assert not orphans, f"docs/reference files CLAUDE.md never links: {orphans}"


def test_the_standing_rules_are_still_in_the_file_itself():
    """These are instructions, not detail: they must be read every session, so
    they may not be relocated behind a link."""
    text = _text()
    for must in (
        "THE DEVELOPMENT RULE (mandatory)",
        "ssh vps2 'cd /home/administrator/dev && tools/promote.sh'",
        "STANDING RULE — configurable by default",
        "Styling is Tailwind-first (mandatory)",
        "No page may `import main`",
        "Never commit real keys, tokens, or account numbers",
        "Correct in place; never append a correction",
        "Compare the failing SET",
        "Never change a bind to `0.0.0.0`",
        "paper-only",
    ):
        assert must in text, f"CLAUDE.md lost a standing rule: {must!r}"


def test_the_relocated_detail_was_kept_not_deleted():
    """The relocation is verbatim. Spot-check one distinctive sentence from each
    moved block, so a reference file cannot be emptied without a failure."""
    probes = {
        "webgui-shell.md": "NAV_SECTIONS",
        "public-live-screens.md": "require_read_only",
        "webgui-dev-notes.md": "A NaN clamps to the HIGH bound",
        "config-files.md": "toml_loader",
        "running-and-environments.md": "guard_prod_promote.py",
        "testing.md": "allow_live_db",
        "options-engine-invariants.md": "settlement_underlying",
        "observability-and-performance.md": "degrades_total",
    }
    for name, needle in probes.items():
        body = (REFERENCE / name).read_text(encoding="utf-8")
        assert needle in body, f"docs/reference/{name} lost {needle!r}"
        assert len(body) > 15_000, f"docs/reference/{name} is suspiciously short"
