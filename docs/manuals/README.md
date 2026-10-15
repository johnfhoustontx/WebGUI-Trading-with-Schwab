# Manuals

Generated documentation for the WebGUI Trading with Schwab stack. Five manuals,
each authored once in Markdown and built into a styled **HTML** (online viewing,
and opens cleanly in Word) plus a native **.docx** (with a Word table-of-contents
field and page numbers).

| Manual | Audience | Folder |
|--------|----------|--------|
| **User Guide** | End users operating the app — task by task | `user-guide/` |
| **Reference Guide** | End users — what each tab/sub-tab is for, why it matters, when to use it | `reference-guide/` |
| **Technical Reference** | Maintainers — all calculations, formulas, weights, cadences | `technical-reference/` |
| **API / Developer Reference** | Developers — contracts, bus API, service commands, proxy endpoints | `api-reference/` |
| **Options Glossary** | End users — the vocabulary the other four assume | `glossary/` |

The User Guide and the Reference Guide are deliberately different documents: the
User Guide answers *how do I do this*, the Reference Guide answers *what is this
for and when should I open it*. Both are written for a reader with moderate
markets/options knowledge, and the Reference Guide cites external sources for the
concepts the app assumes but never teaches.

Each folder contains `<name>.md` (the source), `<name>.html`, and `<name>.docx`.

## Rebuilding

The source of truth is the `.md` file in each folder. After editing it, rebuild:

```powershell
cd docs\manuals
python build_docs.py                 # build all five
python build_docs.py user-guide      # build just one
```

`build_docs.py` converts Markdown → HTML (via the `markdown` package, with a styled
template) and HTML → .docx (via a BeautifulSoup walk into python-docx). No pandoc
or LibreOffice required.

### Architecture diagrams

The diagrams live in `diagrams/`, each drawn once by `diagrams/make_diagrams.py`
(matplotlib), which writes `<name>.svg` and `<name>.png`. A manual places one with a
paragraph of its own:

```text
{{diagram: system-overview | The processes and how they talk}}
```

The HTML build inlines the SVG, so each manual stays one self-contained file (the
app serves no other path). The `.docx` build embeds the PNG. An unknown name fails
the build, and `tests/test_manual_diagrams.py` checks that every placed diagram
exists in both formats and that every drawn diagram is placed. To change one, edit
`make_diagrams.py`, run it, look at the PNG, then rebuild the manuals that place it.
Draw what runs; draw anything not built yet dashed and labelled "planned".

### Dependencies

`markdown`, `beautifulsoup4`, `lxml`, and `python-docx` (all already in the project
venv). Run with the project interpreter.

## Notes

- The `.docx` table of contents is a live Word field — open the file and choose
  **Update Field** (or press F9) to populate it.
- The `.html` files are self-contained (CSS is embedded); they can be opened
  directly in a browser or in Word.
- The Markdown `[TOC]` marker at the top of each source file produces the in-page
  HTML contents box; the .docx uses the native Word TOC field instead.
