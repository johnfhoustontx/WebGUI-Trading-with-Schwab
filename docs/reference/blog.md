# The site Blog

> The detail behind the Blog's rules in `CLAUDE.md`: what each piece does, why
> it is built that way, and what was and was not checked. It is a living
> reference. Correct it in place, exactly as the rules in `CLAUDE.md` say, and
> never append a correction under a wrong sentence.

**What exists (built 2026-10-06, reduced scope).** A Blog on the public site
`neuralstrike.co`. The owner opens More → Blog in the private app, uploads one
self-contained HTML document, previews the cleaned draft, edits its title,
summary, address and tags, and presses Publish. `blog_svc` (port 8217) cleans
the document in a worker process, copies its Google Fonts typefaces onto the
box, stores it, and writes it into the served site. Visitors get a Blog item in
the site menu, a list at `blog.html`, and each entry on its own page inside a
sandboxed frame.

**What does not exist.** The connector that would let Claude Chat file drafts
directly. See [Parked: the connector](#parked-the-connector).

Per-page behaviour of `/blog` is in [webgui-routes.md](../webgui-routes.md); the
design and plan are
[`plans/2026-10-06-site-blog-design.md`](../plans/2026-10-06-site-blog-design.md)
and
[`plans/2026-10-06-site-blog-plan.md`](../plans/2026-10-06-site-blog-plan.md).
Both describe the larger feature; this file describes the code.

## The pieces

| Piece | File | Role |
|---|---|---|
| The shared contract | `shared/blog_inbox.py` | Stream and view names, command builders, validators, limits, the sandbox and policy strings, the store's folder names, the two file readers, `config/blog.toml`'s loader |
| The cleaner | `services/blog_svc/clean.py` | A submitted document in, a rebuilt one out, with a count of what was taken out |
| Its bound | `services/blog_svc/clean_bound.py`, `clean_worker.py` | Runs the cleaner in a worker process and kills it at a time limit |
| Typefaces | `services/blog_svc/fonts.py` | Fetches what an entry asks Google Fonts for and writes its own `@font-face` rules |
| The store | `services/blog_svc/store.py` | `blog.db` and the files beside it: the source of truth |
| The site writer | `services/blog_svc/sitewriter.py` | The only writer of the blog's public files |
| Commands | `services/blog_svc/handlers.py` | `cmd:blog`'s four commands, the three views, the answer |
| The background job | `services/blog_svc/scheduler.py` | Repair and rebuild at start, then the rebuild and the views again on a timer |
| The private page | `webgui/pages/blog.py`, two routes in `webgui/main.py` | Upload, preview, publish, unpublish |
| The public page | `deploy/site/blog.html`, `deploy/site/assets/blog.js` | The list of entries |
| The edge | `deploy/caddy/generate_caddyfile.py` (`@blog_entries`, `BLOG_BARE_PATHS`) | The policy header on each entry document; `/blog` and `/blog/` redirected to `blog.html` |

## The data flow

1. **Upload.** The page reads the file, refuses anything that is not UTF-8
   text (a byte-order mark is tolerated, a NUL is not) or is larger than
   `[limits] max_html_kb`, and builds a `draft_submit` command with
   `blog_inbox.submit_command`. If the owner chose an entry under **Replace an
   existing entry**, the page hands the builder that entry's address as
   `revises`; the builder refuses an address that could not be an entry's, and
   leaves the key out of a command for a new entry. The command goes on
   `cmd:blog` through `bus_client.request`.
2. **Draft.** `blog_svc` runs every validator again on what it reads, refuses a
   new draft when `[limits] max_drafts` are already waiting, cleans the
   document in a worker process, copies its typefaces, fills the typeface rules
   into the cleaned document, and stores the result under
   `staging/<draft id>/`. It stamps the draft's source as `upload` whatever the
   command says. Nothing is written to the site.
3. **Preview.** The page frames `/blog/preview/<draft id>/entry.html`, a private
   route that serves the staged file under the same policy the public site
   sends.
4. **Publish.** The page sends `publish` with the four fields as edited. The
   service copies the draft into `published/<address>/`, deletes the draft,
   rebuilds the public files and drops typefaces nothing names any more.
5. **The site.** The site writer writes `blog/<address>/entry.html` (the
   document), `blog/<address>/index.html` (the site menu around a frame),
   `blog/fonts/`, `blog/sitemap.txt` and, last, `blog.json`.
6. **A visitor.** `blog.html` loads `blog.json` and draws the list. An entry's
   page frames its document with the `sandbox` attribute; Caddy sends a
   Content-Security-Policy on the document itself.

Every command is answered on `cache:blog:result`, a refusal as much as a
success, so the page that sent it is never left waiting on the service.

## The store

`services/blog_svc/data/` (`repo_paths.BLOG_DATA`, gitignored):

```
blog.db                         the rows: drafts, entries
staging/<draft id>/entry.html   a draft's cleaned document, typefaces applied
published/<address>/entry.html  an entry's document
fonts/<20 hex>.woff2            ONE pool of typefaces, named by content
```

The store is the source of truth. The public folder is not: the site writer
rebuilds it whole from the store, so everything a rebuild needs is here. The
nightly backup carries `services/blog_svc/data`; it does not carry
`deploy/site/blog/`, which is rebuilt.

The folder and file names are `shared.blog_inbox`'s (`STAGING_DIR`,
`PUBLISHED_DIR`, `FONTS_DIR`, `DOC_NAME`, `NEXT_NAME`), because the private
preview reads this folder without importing the service.

A document says `../fonts/<name>` for a typeface. On this disk that would be
`staging/fonts/`, which does not exist, and that is intended: the preview route
and the site writer each map that address onto the one pool. There is never a
second copy of a typeface per draft.

### One writer

One process may write here: the service. Every `Store` in that process shares
one lock, held around each public call including its file steps. A second
process may read, through `blog_inbox.read_document` and `blog_inbox.read_font`
and nothing else. A second writer process is not supported: the steps that come
after a commit (a rename into place, the removal of a discarded folder) run
under the in-process lock only.

### A document is the one its row describes, or it is not there

Each row carries `digest`, the SHA-256 of its document's bytes.
`read_document(folder, digest)` tries `entry.html`, then `entry.html.next`, and
returns whichever has that digest, or `None`. Two things follow.

- **A row and a file cannot change in one step**, so for a moment they
  disagree. The digest says which file belongs to the row.
- **What is previewed is what was cleaned, and what is served is what was
  previewed.** A file altered on disk reads as missing. A typeface likewise:
  `read_font` returns one only when its content hashes to its own name.

This is why nothing may read a document by opening `entry.html` directly: while
a replacement is being put in place, that file can be the previous document.

### The order of every write

Everything happens under the lock and inside one `BEGIN IMMEDIATE` transaction,
so the commit is the moment a change has happened and each file step sits on
one side of it.

- **A new document in a new folder** (a new draft; the publish of a new
  entry): typefaces, then the folder and `entry.html` written whole, then the
  row, then commit. Stopped before the commit, the leftover is a folder no row
  names.
- **A document that replaces one** (the publish of a replacement): the new
  document is written beside the old as `entry.html.next`, then the row with
  the new digest, then commit, then the rename over `entry.html`. The old file
  is not touched until the rows say it is replaced. Stopped after the commit,
  the row describes the `.next`; reads already return it, because the digest
  picks it, and the rename is finished by the next read, the next replacement
  or `repair`.
- ⚠ **A `.next` can be the only copy of a committed document.** After the
  commit the rename can be refused (on Windows, by anything holding
  `entry.html` open) and the call still succeeds. So a later replacement never
  writes its own `.next` over one that matches the row and never deletes a
  `.next` it did not write: it finishes the waiting rename first and refuses
  with `busy` if it cannot.
- **Publish copies; it does not move.** The draft's row and staged folder stay
  whole until the commit that creates the entry also deletes the draft. A crash
  at any point loses nothing.
- **Discard and unpublish** remove the row first, then the folder.

Each file is written to a temporary name in its own folder, flushed, then
renamed, so a name never holds half a file.

### What `repair()` does

Run once when the service starts, before the site is rebuilt.

| Found | Done |
|---|---|
| A staged folder no draft names | Removed |
| A draft whose document is not there | The rename is finished if its `.next` matches; otherwise the draft is removed and logged by id |
| A published folder no entry names | Removed |
| An entry whose document is not there | The rename is finished if its `.next` matches; otherwise the entry is KEPT and reported |
| A rename that is still refused | Recorded; nothing is missing |
| A `.next` beside a document that matches | Deleted |
| A half-written temporary file | Deleted |
| A typeface a row names that is not there | Reported |

An entry without its document is kept because losing a published entry in
silence is worse than a broken page; the scheduler logs it as a warning and
counts it on `/health` (`blog.repair`). Each step is its own: one that fails is
recorded by kind and the rest still run. Repair deletes only what the store
could have made: a real folder named like an id under `staging/`, like an
address under `published/`, a temporary file of its own pattern. A link, and
anything else, is listed and left.

### Names

Three kinds of string become a file or folder name, and each has one gate.

| Name | Gate |
|---|---|
| A draft id | `blog_inbox.is_id` (16 hex characters) |
| An address | `clean_slug` for a new one; `existing_slug` to find or remove an entry that already exists |
| A typeface | `FONT_NAME_RE` (20 hex characters, then `.woff2`), and it must be the hash of the bytes stored under it |

`existing_slug` holds an address to the ceiling of `[limits] slug_chars` (120),
not to its current setting (80 as shipped), so lowering the setting cannot
strand an entry published under the old one. The reserved addresses are
`fonts` (it is the typeface folder; an entry there would be written over it)
and the names Windows gives to devices (`con`, `prn`, `aux`, `nul`,
`com1`–`com9`, `lpt1`–`lpt9`), refused on every platform so an entry no Windows
box can restore or test is never published.

Every pattern ends in `\Z`, not `$`: `$` also matches before a final newline,
and each of these strings becomes a name.

## Cleaning

### What it is for

Cleaning is not the security boundary. The frame's `sandbox` and the edge's
policy each stop a script on their own. Cleaning still matters for three
reasons.

- **The preview has to be honest.** The owner approves what is staged. If a
  script or a tracking pixel were left in for the browser to block, the draft
  would say nothing was removed about a document that tried to do something.
- **The policy has holes on purpose.** `style-src 'unsafe-inline'` is what lets
  an entry carry its design, and CSS can fetch (`url()`, `@import`,
  `image-set()`). The policy stops the request leaving the site; the cleaner
  stops it being made.
- **A layer that depends on a header is one misconfigured edge away from
  nothing.**

### The output is built, never edited

The submitted tree is only read. Every tag and attribute name written in the
result comes from the cleaner's own lists, every value and text run is escaped,
and CSS is tokenised, filtered and serialised afresh. No comment, processing
instruction, CDATA section or doctype is copied. That is what makes the result
hold whatever the parser did: a tree can be surprising, but it cannot contain a
tag the cleaner did not choose to write.

Every entry gets the same shell: a doctype, `<html>` (with `lang="en"` unless
the entry sets its own), a charset and a viewport `meta`, the title, one
`<style>` holding a mark for the typeface rules, the entry's stylesheets, then
the body. The entry's own `<html>` and `<body>` tags hand on `class`, `style`,
`lang`, `dir` and `data-*` attributes.

The result is cleaned again until cleaning it changes nothing (at most
`MAX_PASSES`, 5). A document that never settles is refused.

### What is kept

- Text and structure: headings, paragraphs, lists, tables, `details`, `figure`,
  `blockquote`, `code`, `pre` and the inline text elements (`KEPT_HTML`).
- Inline drawings: `svg` with its shapes, text, groups, gradients, clip paths
  and markers (`KEPT_SVG`).
- The entry's `<style>` sheets and `style` attributes, filtered.
- A fixed list of attributes (`ATTRS`), plus `aria-*` and `data-*`. At most 64
  attributes on one element (`MAX_ATTRS`).
- Links to `http://`, `https://`, `mailto:` and in-page `#fragment` addresses.
  A link that leaves the page is given `target="_blank"` and
  `rel="noopener noreferrer"`, because a framed document cannot navigate the
  page around it.

### What is removed

- **With their content:** `script`, `noscript`, `iframe`, `frame`, `frameset`,
  `object`, `embed`, `applet`, `form` and its controls, `template`, `audio`,
  `video`, `canvas`, `img`, `picture`, `map`, `base`, `meta`, `link`, `dialog`
  (`DROPPED`). Images are on the list on purpose: this version of the blog
  carries none, and an `<img>` is a request to whoever it names.
- **Inside a drawing:** `script`, `foreignObject`, `image`, `use`, the
  animation elements, links, and `mask`, `pattern`, `symbol`, `filter`,
  `metadata` (`DROPPED_SVG`).
- **Event-handler attributes** (`on*`), counted.
- **Any other link address:** every other scheme (`javascript:`, `data:`,
  `file:` and the rest) and every relative address. An address holding a tab,
  a line break or an invisible character is refused whole.
- **In CSS:** `@import`, `@namespace`, `@font-face`, `@charset`; any `url()`
  that is not a reference to an id in the same document; the fetching
  functions (`image()`, `image-set()`, `cross-fade()`, `element()`, `src()`,
  `expression()` and their prefixed forms), replaced by `none`; the properties
  `behavior`, `-ms-behavior` and `-moz-binding`.

Two removals are silent by choice: an element not on either list is unwrapped
(its text stays), and a disallowed attribute that is not a handler is dropped.
Everything else is counted in the draft's `removed` map, which the page turns
into one sentence ("Removed 2 scripts and 1 form.").

The cleaner also reports the typeface stylesheets the entry asked for: a
`<link rel="stylesheet">` whose address matches
`https://fonts.googleapis.com/css2?...` and nothing else.

### Why the cleaner runs in a worker process

The HTML parser's cost is not linear in its input. One tag with tens of
thousands of attributes takes minutes at the size limit: 216 s was measured for
a 512 KB document on libxml2 2.11.9.

A scan of the text before parsing was tried as the bound, and failed review
three times. Each time the fault was a shape that the scan and the parser read
differently: a scan that trusts quotes can be fooled through them, and one that
does not refuses an honest chart, whose path data is thousands of tokens inside
one quoted attribute.

So the bound no longer predicts the parser. `clean_bound.clean_bounded` runs
each clean in its own process (`python -m services.blog_svc.clean_worker`) and
kills it at `[limits] clean_sec` (20 s as shipped). An overrun is a refusal
(`too_slow`), counted on `/health` as `blog.clean.too_slow`, and the service
thread is free again. That holds for every slow shape, known or not, on any
parser version, and it contains a crash or a memory blow-up inside the parser
too.

- The worker reads the document on stdin and writes one JSON object on stdout.
  The parent trusts none of it: the fields, their types, the refusal code and
  every typeface link are checked again before a `Cleaned` is built.
- The worker inherits an allow-list of environment variables, not the
  service's environment, which holds keys it has no use for.
- On POSIX the worker lowers its own address space to `[limits] clean_mem_mb`
  (512 MB) and its CPU time to `clean_sec` plus 5 s, and runs in its own
  session so a timeout kills the whole process group. On Windows there is no
  `resource` module and no process group: the wall-clock kill of the one
  process is the whole bound.
- Only the last 200 characters of the worker's stderr are ever logged, and
  never its stdout, which is the document.

**The scan stays, and is best-effort.** It still runs first, in-process, as a
fast refusal of the obvious case: a tag written with more than 1,024 attributes
(`MAX_TAG_ATTRS`) is refused as `crowded_tag` without starting a process. It
skips the content of `<script>` and `<style>`, the one place an honest `<` and
`{` crowd together, and an exotic nesting where the parser builds tags inside
one slips past it. Nothing depends on the scan being complete; the worker's
timer is the bound.

The comment beside `clean_sec` asks for it to stay well under `[limits]
max_wait_sec`, so an overrun is answered before the request expires. That key
belongs to the parked connector and nothing reads it today. The age that
applies to a `cmd:blog` command is the scaffold's own: one older than `[age]
replay_max_sec` (900 s, `config/services.toml`) is dropped unrun and answered
`dropped`.

### Two parser versions

`lxml`'s wheels bundle libxml2, and the two platforms bundle different ones:
2.11.9 in the Windows wheel and 2.14.6 in the Linux wheel. The 2.14 line's HTML
tokenizer was rewritten to follow HTML5, so the same bytes can parse into
different trees on the development box and on prod. This is the reason the
output is built from the cleaner's own lists rather than edited.

The blog service suite was run on both. The Windows run is the development
box's ordinary one. The Linux run was done under WSL with the Linux wheels
unpacked there, on Python 3.14, which is **not** prod's Python version. It
shows the suite passes against the Linux parser; it is not a run on prod.

## Typefaces

An entry written in Claude Chat asks Google Fonts for its typefaces. The public
site loads nothing from another origin, so the service fetches those files once,
at draft time, keeps them in the store's pool, and fills the entry's marked
`<style>` with `@font-face` rules that point at the local copies. The entry is
served at `/blog/<address>/entry.html` and a typeface at `/blog/fonts/<name>`,
which is why every rule says `../fonts/<name>`.

### Parsed and rebuilt, never pasted through

Nothing cleans a document after the typeface rules are added (the cleaner
drops every `@font-face` it meets, these included), so the CSS `fonts.py`
writes is trusted by everything after it. Google's stylesheet is therefore only
read:

- It is parsed with `tinycss2`, the tokenizer the cleaner uses, so an escaped
  `url(` is a url here too.
- A block is copied only when it is labelled, by the comment directly before
  it, with one of `[fonts] subsets` (`latin` and `latin-ext` as shipped). An
  unlabelled block is not copied.
- A block is used only when it holds declarations alone, no listed descriptor
  twice, a family and a `src`, and exactly one `url()` in the whole block.
- `src` must be exactly one `url()` on `fonts.gstatic.com` ending `.woff2`,
  followed by a `format()` naming WOFF2.
- Each rule is written again from a table of descriptors, each with a value
  the module understood and spelled itself. A family name is written with
  every character outside `[A-Za-z0-9 _-]` as a hex escape. The one address is
  `../fonts/<20 hex>.woff2`, where the name is a hash of bytes the module
  fetched and checked.
- The finished text is held to a pattern built from that same table before it
  leaves `localize`, and again in `apply`. Rules that do not match go in as
  nothing, and that is counted (`blog.fonts.apply`).

A fetched file must be a whole WOFF2 file as far as its 48-byte header says:
the `wOF2` signature, at least one table, and a total length equal to the
number of bytes received.

### What is fetched

Two hosts and no others: a stylesheet from `fonts.googleapis.com`, a file from
`fonts.gstatic.com`, both over https. The address is checked on its text before
anything connects, by equality of the host part with one of the two names,
which refuses userinfo, a port, a trailing dot, another case and a look-alike
in one comparison. No redirect is followed, and anything but a plain 200 is a
failure. The body is counted as it arrives, in decoded bytes.

### The caps (`config/blog.toml [fonts]`)

| Key | Shipped | Bounds |
|---|---|---|
| `max_links` | 4 | Typeface stylesheets followed for one entry |
| `max_css_kb` | 256 | The most one stylesheet may send back |
| `max_files` | 24 | Typeface files copied for one entry |
| `max_file_kb` | 400 | The largest single file stored |
| `max_total_mb` | 12 | All of one entry's files together; they are held in memory at once |
| `max_rules` | 96 | The `@font-face` rules written into one entry |
| `timeout_sec` | 10 | One request |
| `total_sec` | 30 | All of one entry's requests together |

`max_files` does not bound the rules, because many rules can name one file, so
`max_rules` does. One rule can be at most 3,075 characters
(`RULE_CHARS_CEILING`), so 96 rules add under 300 KB to an entry whose own size
limit was applied before any of this. One rule's `unicode-range` may list at
most 128 ranges (`MAX_RANGES`).

`total_sec` is the cap that protects the command queue: 24 files timing out one
after another at 10 s each would hold the service for four minutes. The service
copies typefaces before it reads its next command.

`user_agent` is who the service says it is. Google chooses what to send by who
is asking: a client it does not know is sent TrueType with no character-set
split, a desktop Chrome is sent WOFF2. The string ages. If drafts start saying
their typeface rules were not usable, put a current desktop Chrome's
User-Agent there; that needs no code change.

### It never blocks a draft

`localize` raises nothing that is an `Exception`. A link or a file that fails
costs only itself; the entry falls back to the fonts its own stylesheet names,
and the draft carries one sentence saying so, made of the module's own words
and counts. A typeface that was not copied is counted, never named. A failure
that is the service's own rather than the network's is counted on `/health`
under `blog.fonts.fetch`, `blog.fonts.reader`, `blog.fonts.apply` or
`blog.fonts.guard`.

A replacement that asks for no typefaces keeps the ones its entry has.

## The three isolation layers

Any one of them stops a script.

1. **Cleaning** removes it before the document is stored.
2. **The frame's `sandbox`.** The entry's page, and the private preview, frame
   the document with
   `sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"`.
   No `allow-scripts`, no `allow-forms`.
3. **The edge's policy.** Caddy sends this on `/blog/<address>/entry.html`, so
   the same holds when a visitor opens the document outside its frame:

   ```
   default-src 'none'; style-src 'unsafe-inline'; font-src 'self'; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'; sandbox allow-same-origin allow-popups allow-popups-to-escape-sandbox
   ```

   `img-src` is `data:` alone, not `'self'`. The cleaner removes every image
   and every CSS `url()` that leaves the document, so `'self'` would give an
   entry nothing; and the private preview is served on the app's origin, where
   `'self'` is the one source that would let a miss in the cleaner send a
   request carrying the session cookie to one of the app's own routes.

Both strings are defined once, as `blog_inbox.ENTRY_SANDBOX` and
`blog_inbox.ENTRY_CSP`, and imported by the three places that must agree: the
site writer (the frame), the Caddyfile generator (the header) and the private
preview route (both). Change the policy there, never in a generated file.

The Caddy rule names only the framed document
(`^/blog/[a-z0-9-]+/entry\.html$`). The entry's own page is `index.html` in the
same folder and must not match: a `sandbox` and `default-src 'none'` on that
page would switch off the site's stylesheet and menu. The pattern is looser
than `SLUG_RE` on purpose (it admits a doubled hyphen, which can only name a
404) and must never be tighter, because a missed entry is served with no
policy.

### Why `allow-same-origin`, and why never `allow-scripts`

`allow-same-origin` is there for the typefaces. Without it the framed document
has an opaque origin and its font requests are cross-origin: the site would
need CORS headers on `/blog/fonts/`, and the private preview's font requests
would carry no session cookie and be refused by the login.

With `allow-same-origin` and no `allow-scripts`, nothing runs, so the shared
origin gives the document nothing to use. The two together would let a framed
document remove its own sandbox. So `allow-scripts` must never be added, to the
frame or to the policy, for any reason. If a typeface does not load in a frame,
that is a fault to report, not one to fix by loosening either string.

## The bus views

`cache:blog:*` is readable by the public live process. **No view carries a
document, and none may.** The documents are files in the store's folder, which
nothing serves but the private preview.

| View | Payload | Written |
|---|---|---|
| `blog:drafts` | `{"drafts": [...]}`, newest first. Each row: `id`, `source`, `revises`, `slug`, `title`, `summary`, `tags`, `removed`, `font_note`, `bytes`, `digest`, `received_at` | `skip_unchanged`, so it holds no timestamp of its own |
| `blog:posts` | `{"entries": [...]}`, newest first publication first. Each row: `slug`, `title`, `summary`, `tags`, `published_at`, `updated_at` | `skip_unchanged` |
| `blog:result` | `{"request_id", "command", "ok", "message", "draft_id", "slug"}`, always all six keys | Plainly, so the same refusal twice is two answers |

The row keys are named in `handlers.DRAFT_KEYS` and `handlers.ENTRY_KEYS`, so
nothing else in the store (a draft's typeface list, a column added later)
reaches a key the public process can read without being added on purpose.

The two list views are read from the store and written as one step under a
lock, because the command thread and the scheduler both publish them.

## The refusal codes, and what the page is shown

A refusal travels as a code and is turned into a sentence in one place,
`handlers.MESSAGES`. An exception's text is never shown and never logged,
because it can quote a path or a fragment of the document.

| Code | From | Sentence |
|---|---|---|
| `empty`, `not_text` | cleaner | The file had no content that could be shown. |
| `crowded_tag`, `too_deep`, `cut_off` | cleaner | The file could not be read to its end. It may be cut off or malformed. |
| `too_slow` | the worker's timer | Cleaning the file took too long, so it was refused. |
| `did_not_settle`, `internal` | cleaner | The file could not be cleaned because of a fault in the cleaner. It has been logged. |
| `slug_taken` | store | An entry already uses this address. Choose another address, or upload the file as a replacement for that entry. |
| `slug_changed` | store | A replacement must keep the address of the entry it replaces. |
| `no_title` | store, handler | Give the entry a title before publishing. |
| `no_draft`, `document_missing` | store | That draft is no longer there. |
| `busy` | store | The store is busy finishing an earlier change. Try again in a moment. |
| `bad_name`, `bad_input` | store, handler | That request was not understood. |
| `bad_document` | handler | The file was empty, or larger than the N KB an upload may be. |
| `full` | handler | There are already N drafts waiting. Publish or discard one, then upload the file again. |
| `too_large` | handler | Once cleaned, the file was too large to keep. Upload a smaller one. |
| `revises_gone` | handler | The entry this file was meant to replace is not published any more. Upload the file as a new entry instead. |
| `bad_address` | handler | That address cannot be used. Use lower-case letters, digits and single hyphens, at most N characters. |
| `not_published` | handler | The draft could not be published. Its address may already be in use, or the draft may have been removed. |
| `no_entry` | handler | That entry is not published. |
| `fault` | handler | The request could not be completed because of a fault in the service. It has been logged. |
| `dropped` | the scaffold's notice | The request waited too long and was not carried out. Try it again. |

`too_large` is a staged document over six times `max_html_kb` plus 512 KB.
Cleaning can multiply a document's size about six times (every `"` inside an
attribute is written `&quot;`), and the typeface rules come to about 300 KB at
their own limits.

A command that worked is answered the same way: `filed`, `published`,
`discarded`, `unpublished`, and four that say the store changed but the site
did not: `published_site_off` and `unpublished_site_off` (`[site] enabled` is
false; the second says the entry's page is still on the site, because a rebuild
with the site off removes nothing), `published_site_failed` and
`unpublished_site_failed` (the write is retried by the scheduler). The
scheduler rebuilds the site at every pass (`[site] republish_min`), so a site
switched back on catches up within one interval without a restart.

A replacement draft is offered with the tags of the entry it replaces (an
upload carries none); its title and summary are the new document's.

**`handle_command` never raises.** The scaffold dead-letters a command whose
handler raised, whole, and a `draft_submit` carries the entire document. A
fault is answered, counted (`blog.handlers`) and goes no further.

## The public files

All under `deploy/site/`, all gitignored generated state like `ideas/` and
`reports/`: tracked, the first published entry would dirty prod's tree and
`tools/promote.sh` refuses a dirty tree.

| Path | Purpose |
|---|---|
| `blog.json` | The list `assets/blog.js` draws |
| `blog/<address>/index.html` | The entry's page: the site menu around a frame |
| `blog/<address>/entry.html` | The entry itself, as the store holds it |
| `blog/fonts/<20 hex>.woff2` | Every typeface a published entry uses |
| `blog/sitemap.txt` | The entries' addresses |

`blog.html` and `assets/blog.js` are tracked.

**`neuralstrike.co/blog` is a redirect.** The list is `blog.html`; `/blog/` is
the folder the entries live in and has no index, so the address people type was
a 404. Caddy sends exactly `/blog` and `/blog/` to `/blog.html` with a 308
(`BLOG_BARE_PATHS` in `deploy/caddy/generate_caddyfile.py`). Both are exact
paths: a Caddy `path` matcher with no `*` matches that whole path only, so
`/blog/<address>/`, an entry's document and `/blog/fonts/*` are never
redirected. It takes effect with the same Caddy regenerate and reload the
policy header needs.

- **The menu is the tracked one.** Each entry page carries the `<nav>` of the
  tracked `blog.html`, byte for byte, with its icon links, stylesheets and
  footer. They are read at every rebuild, and the service rebuilds at start, so
  a promote that changes the menu changes every entry page. A checkout whose
  `blog.html` is missing or lacks any of those pieces writes nothing and says
  so. An entry page sets `<base href="/">`, so every address in the menu must
  stay a plain path from the site root.
- **The order.** Typefaces, then each entry's document and page, then the
  sitemap, then `blog.json` last, so a page exists before the list links to
  it. Removal comes after that.
- **What is never deleted.** A page the store could not supply a document for
  is left exactly as it is, and the rebuild is reported not ok. Nothing the
  site writer did not write is removed. A link is never followed.
- **`rebuild` never raises.** A shortfall is counted (`blog.site`) and retried
  by the scheduler.
- **`blog.js` treats the manifest as data.** Every node is built with
  `createElement` and `textContent`. A row is drawn only when its address
  matches the service's own pattern and its title is not empty. With no
  manifest the page says no entries have been published yet.

## What is not verified

- **Caddy itself.** The generated Caddyfile is tested as text and its pattern
  is compiled by the tests. No Caddy has served an entry with the policy
  header. After the first promote, the runbook's check is the proof
  ([dev-prod-environments.md](../dev-prod-environments.md), "The site blog").
- **Safari and Firefox.** Nothing here has been opened in either.
- **The real service process against a real Redis.** The suite drives the
  handlers and the scheduler's two passes as functions on the fake bus.
  Schedulers do not run under pytest, and the worker's POSIX branches (its own
  session, the process-group kill, `setrlimit`) cannot be exercised on the
  Windows development box.
- **The first live typeface copy.** `fonts.py` was written against the shape
  the css2 endpoint is known to send a desktop Chrome, not against an answer:
  the suite cannot reach the network and the build did not. What to check on
  the first live copy is step 5 of Task 12 in
  [the plan](../plans/2026-10-06-site-blog-plan.md).

## Parked: the connector

The design describes a larger feature: a connector, so a conversation in Claude
Chat could file a draft directly. It needs a separate internet-facing process
(`blog_gate`), an OAuth sign-in approved inside the private app, a fourth
hostname and an edge rule for it. The owner cut scope on 2026-10-06 to "the
blog page and a way to upload an HTML file". **None of the connector is
built.**

What the code still carries for it, and what that means today:

- `shared.blog_inbox` names a second stream, `cmd:blog_inbox`
  (`INBOX_STREAM`), and builds `draft_revise` commands (`revise_command`) and
  per-request answer views (`answer_view`). Nothing reads that stream:
  `blog_svc` consumes `cmd:blog` only.
- `config/blog.toml [limits]` has three keys the built service does not read:
  `submissions_per_hour`, `max_wait_sec` and `answer_keep_sec`. They are
  catalogued under Settings → Configuration and changing them changes nothing.
- `config/services.toml [stream_keep]` caps `cmd:blog_inbox` at 50 beside
  `cmd:blog`. The cap is harmless on a stream nothing writes.
- The store has a `replace_draft` method and a submission log no command
  reaches.
- The private page can label a draft's source "Claude Chat". Every draft this
  build makes is stamped `upload`.

The design for all of it is in
[`plans/2026-10-06-site-blog-design.md`](../plans/2026-10-06-site-blog-design.md)
("The connector"); the unbuilt tasks are 14 to 23 of the plan.
