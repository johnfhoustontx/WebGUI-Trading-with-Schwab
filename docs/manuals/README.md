# Manuals

Generated documentation for the WebGUI Trading with Schwab stack. Three manuals,
each authored once in Markdown and built into a styled **HTML** (online viewing,
and opens cleanly in Word) plus a native **.docx** (with a Word table-of-contents
field and page numbers).

| Manual | Audience | Folder |
|--------|----------|--------|
| **User Guide** | End users operating the app | `user-guide/` |
| **Technical Reference** | Maintainers — all calculations, formulas, weights, cadences | `technical-reference/` |
| **API / Developer Reference** | Developers — contracts, bus API, service commands, proxy endpoints | `api-reference/` |

Each folder contains `<name>.md` (the source), `<name>.html`, and `<name>.docx`.

## Rebuilding

The source of truth is the `.md` file in each folder. After editing it, rebuild:

```powershell
cd docs\manuals
python build_docs.py                 # build all three
python build_docs.py user-guide      # build just one
```

`build_docs.py` converts Markdown → HTML (via the `markdown` package, with a styled
template) and HTML → .docx (via a BeautifulSoup walk into python-docx). No pandoc
or LibreOffice required.

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
