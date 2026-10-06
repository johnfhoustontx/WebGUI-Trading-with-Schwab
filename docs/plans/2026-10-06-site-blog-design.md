# A Blog on neuralstrike.co, fed from Claude Chat — design

**Date:** 2026-10-06 · **Status:** approved, not built

## Ask

"Add a Blog page to the Website. This page will receive entries from Claude Chat."

The website is the public marketing site, `neuralstrike.co` (`deploy/site/`).
Claude Chat is claude.ai. An entry is a document Claude Chat has written as an
artifact; the operator's example is `Nuclear Stocks Thesis.html`.

## Decisions

| Question | Answer | Why |
|---|---|---|
| How does an entry reach the box? | A **custom connector**: an MCP server on the box that claude.ai calls | The operator's choice, over a Drive folder, pasting, or a workstation uploader. |
| What happens to a received entry? | It is a **draft**. The operator previews it in the private app and presses Publish | A chat that has read a web page or a file can be steered by what it read. Nothing a chat writes reaches the public site unseen. |
| What is an entry? | A **self-contained HTML document**: its own stylesheet, inline SVG, tables, outbound links | That is what the example is. A Markdown body cannot carry it. |
| How is the connector protected? | Its **own hostname and process**, **OAuth** sign-in approved inside the private app, the edge admitting only Anthropic's range | Same reasoning as the three existing origins: a separate origin is a boundary a misconfiguration cannot merge. |
| The entry asks Google for typefaces | They are **copied onto the box** at draft time | The site loads nothing from another origin today, and keeps that property. |
| A second way in | An **upload** control on the private Blog page | A connector call re-sends the whole document as text (see "Cost of the connector path"). Upload is also usable before the connector's operator steps are done. |

## What the example file is

Measured on `Nuclear Stocks Thesis.html` (35 KB, 387 lines), a claude.ai
artifact saved from the browser:

- claude.ai's download wrapper: a `<head>` with a base stylesheet, then the
  artifact source inside `<body>`, starting at its own `<title>`.
- Three `<link>`s to Google Fonts (Schibsted Grotesk, Newsreader, IBM Plex Mono).
- One `<style>` of about 100 rules, with light and dark tokens
  (`prefers-color-scheme`, and `:root[data-theme="dark"]`).
- One inline `<svg>` chart, four tables, 28 anchors to 22 outside hosts.
- **No `<script>` and no event handler.**

The design assumes this shape and refuses to depend on the last point: a later
entry may carry script, and it must not run.

## Shape

```
Claude Chat ──► mcp.<site host> ──► blog_gate   (new process, internet-facing)
                                       │ XADD only: cmd:blog_inbox
                                       ▼
private app /blog ── cmd:blog ──► blog_svc      (new Tier-2 service)
  upload · edit · Publish               ├─ blog.db          drafts, entries, the connection
  Discard · Unpublish · Revoke          ├─ data/staging/    cleaned drafts + their fonts
                                        ├─ cache:blog:*     views the app and the gate read
                                        └─ deploy/site/blog/…  + deploy/site/blog.json
neuralstrike.co/blog.html ◄── assets/blog.js reads blog.json; each entry has its own page
```

Three new pieces, each with one job:

- **`shared/blog_inbox.py`** — pure data and validators: the two stream names,
  the request builders, the size and field checks, the `config/blog.toml`
  loader. Imported by the gate, the service and Tier 1; stdlib plus
  `shared.config_toml` only, pinned by a test like `shared.public_scan`.
- **`services/blog_gate/`** — speaks the connector protocol and OAuth, and
  nothing else. It imports no engine and no part of `blog_svc`.
- **`services/blog_svc/`** — owns drafts and entries, cleans documents, fetches
  typefaces, and is the **only writer of the site's blog files**.

`blog_svc` reads `cmd:blog_inbox` on a consumer loop of its own
(`make_app(extra_consumers=...)`), so a flood of drafts cannot delay the
operator's Publish on `cmd:blog`.

## The connector (`blog_gate`)

### Tools

| Tool | Arguments | Effect |
|---|---|---|
| `submit_draft` | `html`, optional `title`, `summary`, `tags`, `slug` | Files a draft. A `slug` naming a published entry makes it a pending revision of that entry. |
| `revise_draft` | `draft_id`, the same fields | Replaces a draft that is still waiting. |
| `list_entries` | none | Published entries and waiting drafts: slug, title, status, dates. |
| `get_entry` | `slug` or `draft_id` | The stored HTML, so the chat can revise it. |

There is **no publish, unpublish or delete tool**. Changing what the public
sees is the operator's, from the private app.

A tool call is answered the way the public tools are: the gate validates,
XADDs one request, and polls a per-request answer key that expires. Validation
runs in the gate and again in the service.

### Least privilege

- The unit loads **`.env.blog`** and never the stack's `.env`. It needs one
  value, `REDIS_BLOG_URL`.
- Its Redis ACL user may `XADD` to `cmd:blog_inbox` and read `cache:blog:*`.
  No `@write`, no `+publish`, no other stream.
- Prod refuses to serve without that user and proves at start that a `SET` on
  a cache key and an `XADD` on `cmd:blog` are both refused (the
  `live_main.require_read_only` pattern).
- It binds `127.0.0.1`. Caddy is the only thing that talks to it.

### Sign-in

claude.ai connects with OAuth 2.1, PKCE and Dynamic Client Registration. The
`mcp` Python SDK supplies the metadata, registration, authorize and token
routes; the gate implements its storage provider.

1. The operator adds the connector in claude.ai and presses Connect. claude.ai
   registers itself and opens the gate's `/authorize` in the operator's browser.
2. The gate shows a short **one-time code** and waits. It sends the request
   (id, client name, callback address, the code) to `blog_svc` on the inbox
   stream.
3. The operator types the code on the private Blog page, behind password and
   TOTP. `blog_svc` matches it against the pending request.
4. The gate sees the approval in `cache:blog:connect`, issues the authorization
   code and redirects to claude.ai's callback. Token exchange is standard.

Rules:

- **The code is typed, not clicked.** A connection someone else started shows
  its code only in their browser, so the operator cannot approve it by mistake.
  This is the device-flow user-code pattern.
- The code never appears in a cache view: `cache:*` is readable by the public
  live process. It travels on the stream and is stored hashed.
- A pending request expires (`[connect] code_ttl_min`) and is void after
  `[connect] code_tries` wrong codes.
- **Registration is refused** unless every redirect URI is in
  `[connect] redirect_uris` (shipped: claude.ai's and claude.com's
  `/api/mcp/auth_callback`).
- **One connection at a time.** Approving a new one replaces the old.
- **Revoke** on the private page bumps an epoch `blog_svc` publishes; the gate
  refuses every token issued under an older epoch.
- Registered clients and tokens live in a small file the gate owns
  (gitignored, mode 0600, tokens stored hashed), so a promote does not
  disconnect the operator.

### The edge

A fourth Caddy block, `MCP_HOST` (`mcp.<site host>`, overridable in
`env.local.toml` like the other hosts):

- `/mcp`, `/token` and `/register` admit only the ranges in
  `config/edge.toml [mcp] allow_ranges` (shipped: `160.79.104.0/21`, the range
  Anthropic documents for outbound MCP calls). `[mcp] restrict = false` turns
  the filter off, for diagnosis if Anthropic's side behaves differently.
- `/authorize`, `/connect/*` and the `.well-known` metadata are open: the
  first two are the operator's browser.
- A request body limit (`[mcp] body_kb`).
- `robots.txt` answers `Disallow: /`.
- The reverse proxy stamps `X-Edge`, like every other proxied route.

The range is shared by every Claude user. It narrows the door; OAuth is the
credential.

### Cost of the connector path

A connector tool receives only what the model writes into the call. Claude
Chat therefore re-sends the whole document as text: about 10,000 tokens and a
minute or two for the 35 KB example, and the copy can differ from the artifact
the operator saw. The draft preview is the check. This is also why upload
exists.

## Cleaning (`blog_svc/clean.py`)

The security boundary is structural (next section). Cleaning is hygiene, and
makes the preview honest: what is staged is what will be served.

- Parsed with `lxml`, rebuilt into a clean document shell. claude.ai's wrapper
  head is dropped.
- **Kept:** text and semantic elements, tables, inline SVG drawing elements,
  `<style>`, `style=`, anchors to `http`, `https`, `mailto` and in-page
  fragments.
- **Removed:** `script`, event-handler attributes, `iframe`, `object`, `embed`,
  `form` and its controls, `base`, `meta http-equiv`, every `<link>`, any CSS
  `url()` or `@import` that does not name a local font, any other URL scheme.
- **Images are removed in this version.** The example has none.
- Every outbound anchor gets `target="_blank" rel="noopener noreferrer"`.
- Title falls back to the document's `<title>`, then its `<h1>`. Summary falls
  back to the first paragraph, cut to `[limits] summary_chars`.
- The draft records what was removed ("2 scripts", "1 form"), shown beside it.

## Typefaces (`blog_svc/fonts.py`)

- Only a `<link rel="stylesheet">` to `fonts.googleapis.com/css2` is followed.
  From what it returns, only `fonts.gstatic.com` `.woff2` URLs are fetched.
- Only the subsets in `[fonts] subsets` (shipped: `latin`, `latin-ext`), under
  count and size caps.
- Files are stored by content hash; the `@font-face` rules are inlined into
  the entry and point at the local copies.
- Fetched **at draft time**, so the preview shows the real typography.
- A failed fetch never blocks: the draft keeps the fallbacks its own
  stylesheet names, and says so.
- The fetch function is injected, because the test suite cannot reach the
  network.

## How an entry is isolated

Three layers, any one of which stops script:

1. **Cleaning** removes it.
2. **The frame is sandboxed**: `sandbox="allow-popups
   allow-popups-to-escape-sandbox"`. No script, no forms, an opaque origin.
3. **The edge sends a policy on `/blog/*/entry.html`**, so the same holds when
   the document is opened outside its frame:
   `default-src 'none'; style-src 'unsafe-inline'; font-src 'self'; img-src
   'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'self';
   sandbox allow-popups allow-popups-to-escape-sandbox`.

⚠ A sandboxed document has an opaque origin, so its font requests are
cross-origin and need `Access-Control-Allow-Origin` on `/blog/fonts/*`. Whether
`font-src 'self'` matches there is browser behaviour to **verify in a browser**,
not to assume.

The private preview serves the staged file with the same header and the same
`sandbox` attribute.

## What lands on the site

| Path under `deploy/site/` | Tracked | Purpose |
|---|---|---|
| `blog.html`, `assets/blog.js` | yes | The Blog page: site menu, heading, the list of entries, newest first |
| `blog.json` | no | The manifest `blog.js` draws |
| `blog/<slug>/index.html` | no | The entry's page: site menu, the entry's title and description, the frame |
| `blog/<slug>/entry.html` | no | The cleaned document |
| `blog/fonts/<hash>.woff2` | no | Typefaces |
| `blog/sitemap.txt` | no | Entry addresses |

- **Gitignored**, for the reason `ideas/` and `reports/` are: a tracked file
  written on the box dirties prod's tree and `tools/promote.sh` refuses it.
- **The manifest sits at the site root**, so Caddy's existing `*.json`
  `no-cache` rule covers it.
- **The entry page's menu is the tracked one.** `blog_svc` copies the `<nav>`
  block byte for byte out of `blog.html`, sets `<base href="/">`, and rebuilds
  every entry page at start, so a promote that changes the menu updates them.
- **Per-entry title, description and social tags** are on the entry page, so a
  shared link previews as that entry.
- **"Blog" joins the menu on every page**, between Trade ideas and Glossary,
  and `blog.html` joins `sitemap.txt`. `robots.txt` gains a second `Sitemap:`
  line for `blog/sitemap.txt`.
- **Colours follow the visitor's setting**, as the entry was authored.
- Missing or malformed manifest: the page says no entries have been published
  yet. Never an empty frame.
- Every write is to a temporary name and renamed. Unpublish removes the
  entry's folder and its manifest row, and prunes fonts no entry uses.

## The private Blog page (`/blog`, More → Blog)

- **Drafts waiting.** Source (Claude Chat or upload), size, received time,
  what cleaning removed. Title, summary, address (slug) and tags are editable.
  Preview, Publish, Discard.
- **Preview** frames the staged file through a private route behind the login.
- **Published.** Open on the site; Unpublish.
- **Upload.** An `.html` file up to `[limits] max_html_kb`, through the same
  cleaning and the same draft step.
- **Connector.** Connected or not, since when; the code field; Revoke.

Tier 1 stays inside its rules: the page enqueues on `cmd:blog` through
`bus_client.request` and reads `cache:blog:*`. It imports `shared.blog_inbox`
(one addition to the allow-list) and no part of `blog_svc`.

## Rules that keep it safe

- **Nothing publishes without the operator.** No stream the gate can write
  carries a publish command; `blog_svc` accepts publish only from `cmd:blog`.
- **`cache:blog:*` is readable by the public live process.** No view holds the
  connection code or a token. Draft HTML is not in a view at all: it is on
  disk in the staging folder, which nothing serves but the private preview.
- **A site write never raises into a command.** A failure answers the request
  with an error and goes through `_degrade.degraded("blog.site")`.
- **Slugs are an allow-list** (`[a-z0-9-]`, bounded length). A slug becomes a
  folder name under the served root.
- **Dev publishes nothing to a served root**: dev's `SITE_ROOT` is its own
  checkout.
- **Limits** live in `config/blog.toml` (`[limits]`, `[fonts]`, `[connect]`,
  `[site]`) and `config/edge.toml [mcp]`, each catalogued in
  `webgui/config_schema.py`.

## Build order

1. **Phase 1 — the blog without the connector.** `shared/blog_inbox.py`,
   `blog_svc` (store, cleaning, typefaces, site writer), the site pages, the
   private page with upload, the `/blog/` edge policy. Usable and promotable on
   its own.
2. **Phase 2 — the connector.** `blog_gate`, the sign-in, the `MCP_HOST` edge
   block, the Connector panel.

Operator steps, one time:

- Phase 1: regenerate and reload Caddy.
- Phase 2: a DNS record for `mcp.<site host>`; the gate's Redis ACL user;
  `.env.blog`; regenerate and reload Caddy; add the connector in claude.ai.

`mcp` joins `requirements.txt` and, by hand, `requirements.lock`. `lxml` is
already in the lock and becomes a named dependency.

## Testing

- **Cleaner:** a hostile corpus (script in every position, handlers, `javascript:`
  URLs, CSS `url()` and `@import`, nested frames, malformed markup) and a
  synthetic entry modelled on the example. The operator's file is not committed.
- **Typefaces:** host allow-list, subset filter, caps, failure path, with an
  injected fetch.
- **Store and site writer:** against a temporary site root: publish, revise,
  unpublish, font pruning, atomic writes, the rebuilt menu matching
  `blog.html` byte for byte.
- **Manifest:** a pure merge function; ordering, replace, malformed input.
- **Gate:** the sign-in end to end; a refused redirect URI; code expiry and
  attempt limit; epoch revoke; tokens stored hashed; the ACL proof at start.
- **Edge:** the `/blog/` policy and CORS headers; the `MCP_HOST` block's range
  filter, body limit and `X-Edge` stamp.
- **Guards updated:** `test_site.py` (seven pages, menu, sitemap, the
  generated prefixes), `.gitignore` pins, the backup file list, the Tier-1
  import allow-list, `test_bus_client.py`'s writer set, the config catalogue,
  CI rows for the two new suites.
- **In a browser.** There is no dev environment. The private page is checked
  in `tools/ui_harness.py`; the site pages on a local static server that sends
  the edge's headers. The behaviour to watch is typefaces inside the sandboxed
  frame.

## Docs that move with it

`docs/CHANGELOG.md`, `docs/webgui-routes.md`, the manuals and
`webgui/page_help.py`, the operator runbook (the phase 2 steps), a
`docs/reference/blog.md` for the detail, and `CLAUDE.md` edited in place where
invariants change: a seventh service, a third internet-facing process and its
port, the new stream, the Tier-1 allow-list entry.

## Not built

- Images in an entry.
- Comments, an RSS feed, a home-page strip of recent entries.
- A publish tool for the chat.
- An editor for the entry's body in the private app: the chat revises, or the
  operator uploads a corrected file.
