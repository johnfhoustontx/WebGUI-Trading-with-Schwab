"""
html_render.py - Styled HTML rendering for the gamma tool's web pages
Version: 1.0.0
Last Updated: 2026-06-07

Pure, GUI-free helpers that turn the Key-Levels markdown doc and the Explain
popup text into nicely formatted, hyperlinked HTML pages. Curated glossary
terms are linked (first occurrence per page) to a Google search so the reader
can dig deeper.

No Tk imports; the `markdown` dependency is imported lazily so importing this
module never fails even if it's absent.

Version 1.0.0 Changes:
- Initial implementation: google_url, linkify, wrap_page, markdown_to_html,
  explain_to_html, render_key_levels_html, render_explain_html.
"""

import html as _html
import re
from urllib.parse import quote_plus

#############################################
# GLOSSARY (term -> Google search query)
#############################################

# Multi-word / distinct terms only — bare ambiguous words (e.g. "gamma",
# "wall", "delta") are intentionally excluded to keep links meaningful.
# Order is not significant for correctness (linkify picks the leftmost, longest
# match), but related terms are grouped for readability.
GLOSSARY = [
    ("gamma exposure", "options gamma exposure GEX dealer hedging"),
    ("delta exposure", "options delta exposure DEX dealer positioning"),
    ("vanna exposure", "options vanna exposure VEX dealer hedging"),
    ("charm exposure", "options charm exposure CHEX delta decay"),
    ("gamma flip", "options gamma flip zero gamma level dealer hedging"),
    ("zero-gamma", "options zero gamma flip level"),
    ("call wall", "options call wall gamma resistance dealer hedging"),
    ("put wall", "options put wall gamma support dealer hedging"),
    ("max pain", "options max pain strike expiration pinning"),
    ("pin risk", "options pin risk expiration"),
    ("0-DTE", "0DTE options trading"),
    ("OPEX", "options expiration OPEX week"),
    ("open interest", "options open interest"),
    ("expected move", "options expected move straddle implied volatility"),
    ("iron condor", "iron condor options strategy"),
    ("straddle", "long straddle options strategy"),
    ("Herfindahl", "Herfindahl Hirschman index concentration"),
    ("put/call ratio", "put call ratio options sentiment"),
    ("implied volatility", "implied volatility options"),
    ("IV percentile", "implied volatility percentile options"),
    ("realized vol", "realized volatility options"),
    ("dealer hedging", "options dealer hedging gamma"),
    ("gamma node", "options gamma node open interest"),
    ("GEX", "options gamma exposure GEX"),
    ("DEX", "options delta exposure DEX"),
    ("VEX", "options vanna exposure VEX"),
    ("CHEX", "options charm exposure CHEX"),
    ("vanna", "options vanna greek"),
    ("charm", "options charm greek delta decay"),
]


def google_url(query):
    """Return a Google search URL for ``query``."""
    return "https://www.google.com/search?q=" + quote_plus(query)


#############################################
# LINKIFY
#############################################

def _anchor(text, query):
    return (f'<a href="{google_url(query)}" target="_blank" '
            f'rel="noopener">{text}</a>')


def linkify(html, glossary=GLOSSARY):
    """Wrap the first occurrence (per page) of each glossary term in a Google
    search anchor.

    Tokenises ``html`` on tags so terms inside tag attributes are never
    matched, never links inside an existing ``<a>...</a>`` span, and scans only
    the untouched tail after each insertion so an injected ``href`` can't be
    re-matched. Leftmost match wins; on a tie the longer term wins. The visible
    anchor text preserves the document's original casing.
    """
    placed = set()
    parts = re.split(r"(<[^>]+>)", html)
    in_anchor = False
    out = []

    # Pre-compile case-insensitive, boundary-aware patterns per term.
    patterns = [
        (term, query,
         re.compile(r"(?<![\w-])(" + re.escape(term) + r")(?![\w-])",
                    re.IGNORECASE))
        for term, query in glossary
    ]

    for part in parts:
        if part.startswith("<"):
            low = part.lower()
            if low.startswith("<a") and not low.startswith("<area"):
                in_anchor = True
            elif low.startswith("</a"):
                in_anchor = False
            out.append(part)
            continue
        if in_anchor or not part.strip():
            out.append(part)
            continue

        rest = part
        rebuilt = ""
        while True:
            best = None  # (start, end, query, matched_text, term)
            for term, query, pat in patterns:
                if term in placed:
                    continue
                m = pat.search(rest)
                if not m:
                    continue
                cand = (m.start(), m.end(), query, m.group(1), term)
                if best is None:
                    best = cand
                else:
                    # leftmost, then longest match wins.
                    if cand[0] < best[0] or (
                            cand[0] == best[0] and cand[1] > best[1]):
                        best = cand
            if best is None:
                rebuilt += rest
                break
            start, end, query, matched, term = best
            rebuilt += rest[:start] + _anchor(matched, query)
            placed.add(term)
            rest = rest[end:]
        out.append(rebuilt)

    return "".join(out)


#############################################
# PAGE TEMPLATE / CSS THEME
#############################################

_CSS = """
:root {
  --bg: #0e1117; --panel: #161b22; --border: #2a313c;
  --fg: #e6edf3; --fg-dim: #9aa7b4;
  --accent: #58c4dd; --accent-2: #7ee787;
  --up: #3fb950; --down: #f85149; --amber: #e3b341;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  line-height: 1.6; font-size: 16px;
}
.wrap { max-width: 880px; margin: 0 auto; padding: 32px 24px 80px; }
h1 {
  font-size: 1.9rem; margin: 0 0 4px; color: #fff;
  border-bottom: 2px solid var(--accent); padding-bottom: 12px;
}
.subtitle { color: var(--fg-dim); margin: 0 0 28px; font-size: 1rem; }
h2 {
  font-size: 1.35rem; margin: 34px 0 10px; color: var(--accent);
  border-left: 4px solid var(--accent); padding-left: 12px;
}
h3 { font-size: 1.1rem; margin: 22px 0 8px; color: var(--accent-2); }
p { margin: 10px 0; }
ul, ol { margin: 10px 0; padding-left: 24px; }
li { margin: 4px 0; }
strong { color: #fff; }
a { color: var(--accent); text-decoration: none; border-bottom: 1px dotted var(--accent); }
a:hover { color: var(--accent-2); border-bottom-style: solid; }
code {
  background: #0b0f14; padding: 1px 6px; border-radius: 4px;
  font-family: "Cascadia Code", Consolas, monospace; font-size: 0.92em;
  color: var(--amber);
}
hr { border: none; border-top: 1px solid var(--border); margin: 26px 0; }
blockquote {
  background: var(--panel); border-left: 4px solid var(--amber);
  margin: 18px 0; padding: 12px 18px; border-radius: 6px; color: var(--fg);
}
blockquote p:first-child { margin-top: 0; }
blockquote p:last-child { margin-bottom: 0; }
table {
  width: 100%; border-collapse: collapse; margin: 16px 0;
  background: var(--panel); border-radius: 8px; overflow: hidden;
  font-size: 0.95rem;
}
th, td { text-align: left; padding: 10px 14px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { background: #1d2530; color: var(--accent); font-weight: 600; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: #1b212b; }
section.explain { margin-bottom: 8px; }
.footer { color: var(--fg-dim); font-size: 0.92rem; }
""".strip()


def wrap_page(title, body_html, subtitle=None):
    """Wrap ``body_html`` in a full HTML document with the dark CSS theme."""
    sub = f'<p class="subtitle">{_html.escape(subtitle)}</p>' if subtitle else ""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_html.escape(title)}</title>\n"
        f"<style>\n{_CSS}\n</style>\n</head>\n<body>\n"
        f'<div class="wrap">\n<h1>{_html.escape(title)}</h1>\n{sub}\n'
        f"{body_html}\n</div>\n</body>\n</html>\n"
    )


#############################################
# CONTENT -> BODY HTML
#############################################

def markdown_to_html(md_text):
    """Render markdown (with tables) to HTML. Lazy `markdown` import."""
    try:
        import markdown  # noqa: WPS433 (intentional lazy import)
    except Exception:
        # Fallback: escape and wrap in <pre> so content is still readable.
        return f"<pre>{_html.escape(md_text)}</pre>"
    return markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "sane_lists"],
    )


_BAR_RE = re.compile(r"^━+\s*(.*?)\s*━+$")
_SUBBAR_RE = re.compile(r"^─+\s*(.*?)\s*─+$")


def explain_to_html(text):
    """Convert the Explain popup text into styled body HTML.

    ``━━ TITLE ━━`` → ``<h2>``; ``── Subtitle ──`` → ``<h3>``; ``•`` lines →
    ``<ul><li>``; a bare ``━━━`` / ``───`` divider → ``<hr>``; every other
    non-blank line → ``<p>``. All text is HTML-escaped.
    """
    parts = []
    bullets = []

    def flush():
        if bullets:
            parts.append(
                "<ul>" + "".join(f"<li>{_html.escape(b)}</li>" for b in bullets)
                + "</ul>")
            bullets.clear()

    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            flush()
            continue
        # A bare divider row is all bar-characters with no title.
        if set(stripped) <= {"━"} or set(stripped) <= {"─"}:
            flush()
            parts.append("<hr>")
            continue
        m = _BAR_RE.match(stripped)
        if m and m.group(1):
            flush()
            parts.append(f"<h2>{_html.escape(m.group(1))}</h2>")
            continue
        m = _SUBBAR_RE.match(stripped)
        if m and m.group(1):
            flush()
            parts.append(f"<h3>{_html.escape(m.group(1))}</h3>")
            continue
        if stripped.startswith("•"):
            bullets.append(stripped.lstrip("•").strip())
            continue
        flush()
        cls = ' class="footer"' if "·" in stripped and "Sentiment" in stripped else ""
        parts.append(f"<p{cls}>{_html.escape(stripped)}</p>")

    flush()
    return "\n".join(parts)


#############################################
# TOP-LEVEL RENDERERS
#############################################

def render_key_levels_html(md_text):
    """Full styled HTML page for the Key-Levels markdown doc."""
    body = linkify(markdown_to_html(md_text))
    return wrap_page(
        "Key Levels Reference",
        body,
        subtitle="What each GEX / Charm / DEX / Vanna level means — and how to act on it.",
    )


_VIEW_LABEL = {"gex": "GEX", "charm": "Charm", "dex": "DEX",
               "vanna": "Vanna", "term": "Term"}


def pinch_section_html(state):
    """Dealer Pinch HTML **fragment** (no <html>/<body>) for embedding in the
    Explain page. Returns "" when there's no state. Leads with an armed/watching
    banner, then the conditions checklist, node/levels table, and playbook.
    """
    if not state:
        return ""

    def _strike(s):
        return f"{s:,.0f}" if s is not None else "--"

    def _ck(v):
        return ("n/a" if v is None
                else ('<span style="color:var(--up)">✓</span>' if v
                      else '<span style="color:var(--down)">✗</span>'))

    regime = state.get("regime", "--")
    armed = state.get("armed")
    conf = state.get("confidence", 0)
    node = state.get("node", {})
    lv = state.get("levels", {})
    c = state.get("conditions", {})
    rv = state.get("rv_trend") or {}

    body = [
        "<h2>Dealer Pinch — Vanna/Charm Exhaustion</h2>",
        f"<p><strong>{'ARMED' if armed else 'WATCHING'} · {regime} · "
        f"{conf:.0f}% confidence</strong> — {_html.escape(state.get('reason', ''))}</p>",
        "<h3>Conditions</h3>",
        "<table><tr><th>#</th><th>Condition</th><th>Met</th></tr>"
        f"<tr><td>C1</td><td>DTE &lt; 5</td><td>{_ck(c.get('c1'))}</td></tr>"
        f"<tr><td>C2</td><td>spot within 1% of dominant OI node</td><td>{_ck(c.get('c2'))}</td></tr>"
        f"<tr><td>C3a</td><td>IV elevated (percentile ≥ 80)</td><td>{_ck(c.get('c3a'))}</td></tr>"
        f"<tr><td>C3b</td><td>short-term realized vol falling</td><td>{_ck(c.get('c3b'))}</td></tr>"
        "</table>",
        "<h3>Node &amp; levels</h3>",
        "<table>"
        f"<tr><th>Dominant node</th><td>{_strike(node.get('strike'))} "
        f"(dominance {state.get('node_dominance', 0):.0%})</td></tr>"
        f"<tr><th>Secondary node</th><td>{_strike(state.get('secondary_node'))}</td></tr>"
        f"<tr><th>Pin target</th><td>{_strike(lv.get('pin_target'))}</td></tr>"
        f"<tr><th>Break trigger</th><td>{_strike(lv.get('break_trigger'))}</td></tr>"
        f"<tr><th>Invalidation</th><td>{_html.escape(str(lv.get('invalidation', '--')))}</td></tr>"
        f"<tr><th>IV percentile</th><td>{state.get('iv_pctile') if state.get('iv_pctile') is not None else '--'}</td></tr>"
        f"<tr><th>Realized vol</th><td>{('falling' if rv.get('falling') else 'not falling') if rv.get('falling') is not None else '--'}</td></tr>"
        f"<tr><th>Forced-hedge bias</th><td>{_html.escape(str(state.get('forced_hedge_dir') or '--'))}</td></tr>"
        f"<tr><th>Time to resolve</th><td>{(state.get('time_to_resolve') or {}).get('dte', '--')} DTE</td></tr>"
        "</table>",
        "<h3>Playbook</h3>",
        f"<p>{_html.escape(state.get('playbook', ''))}</p>",
    ]
    return "\n".join(body)


def render_explain_html(text, pinch_state=None, symbol=None):
    """Full styled Explain page covering all views, with the Dealer Pinch
    section at the top when a pinch state is supplied.

    ``text`` is the combined multi-view explain text (━━ view headers, ── sub
    headers). ``pinch_state`` is the dealer-pinch state dict (or None).
    """
    pinch = pinch_section_html(pinch_state) or (
        "<h2>Dealer Pinch — Vanna/Charm Exhaustion</h2>"
        "<p>No pinch data yet — waiting for the first market-data fetch. "
        "Reopen Explain once the tool shows live data.</p>")
    narrative = explain_to_html(text)
    body = linkify(pinch + "\n" + narrative)
    sym = f" — {symbol}" if symbol else ""
    return wrap_page(
        f"Gamma Tool Explain{sym}",
        body,
        subtitle="Dealer-positioning read across GEX, Charm, DEX and Vanna.",
    )
