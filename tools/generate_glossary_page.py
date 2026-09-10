"""Regenerate the body of the site's glossary page from the manual's markdown.

``docs/manuals/glossary/glossary.md`` is ONE source with TWO artifacts: the
app's fifth manual (built by ``docs/manuals/build_docs.py``) and
``deploy/site/glossary.html`` (built by this). The page was first produced by a
one-shot script that was never committed, so the site test's advice to
"re-run the generator" pointed at nothing; this is that script, kept.

**Only the generated region is rewritten** -- from the table-of-contents
``<nav class="gl-toc">`` through ``</main>`` -- plus the term count in the nav.
The ``<head>``, the sticky nav and the footer are hand-owned page chrome and
survive a regeneration untouched, so a meta description or a nav link can be
edited in the HTML directly.

The markdown shape it reads is the glossary's own and nothing more general:

* ``## N. Title``                 -> a numbered ``<section>`` + TOC entry
* ``**Term** — definition``       -> a ``<dt>``/``<dd>`` pair in a ``<dl>``
* any other prose line            -> an intro ``<p class="gl-p">``
* ``| a | b |`` rows              -> a ``gl-table`` (first row is the header)
* ``*text*``                      -> ``<em>``

``render()`` is pure and returns the page as a string; ``main()`` is the only
thing that touches disk. ``tools/tests/test_generate_glossary_page.py`` fails
when the committed page is not what this emits, so an edit to the markdown that
was never regenerated goes red.
"""
import html
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import repo_paths  # noqa: E402

SOURCE_PATH = (pathlib.Path(repo_paths.REPO_ROOT)
               / "docs" / "manuals" / "glossary" / "glossary.md")
OUTPUT_PATH = pathlib.Path(repo_paths.SITE_ROOT) / "glossary.html"

_REGION_START = '      <nav class="gl-toc"'
_REGION_END = "    </main>"
_COUNT_RE = re.compile(r'<span class="ns-count">\d+ terms</span>')


def _inline(text: str) -> str:
    """Escape, then turn ``*x*`` into ``<em>x</em>``. Escaping first is safe
    because ``html.escape`` never touches an asterisk."""
    return re.sub(r"\*([^*]+)\*", r"<em>\1</em>", html.escape(text, quote=True))


def _slug(title: str) -> str:
    """``Orders, Execution, and Risk`` -> ``orders-execution-and-risk``."""
    return re.sub(r"\s+", "-", re.sub(r"[^a-z0-9 -]", "", title.lower()).strip())


def parse(md: str) -> list:
    """``[(number, title, blocks)]`` where a block is ``("p", text)``,
    ``("dl", [(term, definition)])`` or ``("table", [[cell, ...]])``."""
    sections = []
    for line in md.splitlines():
        heading = re.match(r"^## (\d+)\. (.+)$", line)
        if heading:
            sections.append((heading.group(1), heading.group(2).strip(), []))
            continue
        if not sections or not line.strip() or line.strip() == "---":
            continue
        blocks = sections[-1][2]
        term = re.match(r"^\*\*([^*]+)\*\*\s+—\s+(.*)$", line)
        if term:
            if not blocks or blocks[-1][0] != "dl":
                blocks.append(("dl", []))
            blocks[-1][1].append((term.group(1).strip(), term.group(2).strip()))
        elif line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-+:?", c) for c in cells):
                continue                                  # the |---|---| rule
            if not blocks or blocks[-1][0] != "table":
                blocks.append(("table", []))
            blocks[-1][1].append(cells)
        else:
            blocks.append(("p", line.strip()))
    return sections


def term_count(sections: list) -> int:
    return sum(len(body) for _, _, blocks in sections
               for kind, body in blocks if kind == "dl")


def render_region(sections: list) -> str:
    out = [_REGION_START + ' aria-label="Sections">', "        <ol>"]
    for num, title, _ in sections:
        out.append(f'        <li><a href="#{_slug(title)}"><span class="gl-num">{num}</span>'
                   f"{_inline(title)}</a></li>")
    out += ["        </ol>", "      </nav>", "", '    <main class="gl-body">']
    for num, title, blocks in sections:
        out.append(f'      <section class="gl-sec" id="{_slug(title)}">')
        out.append(f'        <h2><span class="gl-num">{num}</span>{_inline(title)}</h2>')
        for kind, body in blocks:
            if kind == "p":
                out.append(f'        <p class="gl-p">{_inline(body)}</p>')
            elif kind == "dl":
                out.append('        <dl class="gl-list">')
                for term, definition in body:
                    out.append(f"          <dt>{_inline(term)}</dt>")
                    out.append(f"          <dd>{_inline(definition)}</dd>")
                out.append("        </dl>")
            else:
                head, *rows = body
                out.append('        <div class="gl-tablewrap"><table class="gl-table">')
                out.append("          <thead><tr>"
                           + "".join(f"<th>{_inline(c)}</th>" for c in head)
                           + "</tr></thead><tbody>")
                for row in rows:
                    out.append("          <tr>"
                               + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>")
                out.append("          </tbody></table></div>")
        out.append("      </section>")
    out.append(_REGION_END)
    return "\n".join(out)


def render(md: str, page: str) -> str:
    """The page with its generated region and term count replaced."""
    sections = parse(md)
    start = page.index(_REGION_START)
    end = page.index(_REGION_END) + len(_REGION_END)
    page = page[:start] + render_region(sections) + page[end:]
    return _COUNT_RE.sub(f'<span class="ns-count">{term_count(sections)} terms</span>',
                         page)


def main() -> int:
    md = SOURCE_PATH.read_text(encoding="utf-8")
    page = OUTPUT_PATH.read_text(encoding="utf-8")
    OUTPUT_PATH.write_text(render(md, page), encoding="utf-8", newline="\n")
    sections = parse(md)
    print(f"{OUTPUT_PATH}: {len(sections)} sections, {term_count(sections)} terms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
