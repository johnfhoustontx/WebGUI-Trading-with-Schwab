# Scheduled gamma briefings on the Claude subscription — design

**Date:** 2026-09-14 · **Status:** built · **Module:** `services/options_svc/claude_cli.py`

## Problem

The four scheduled gamma briefings (`[slots.analyze]`: premarket 08:00, open 08:48,
midday 11:30, close 15:15 CT) cost **8 billed API calls per trading day** — each
briefing is a news-research call with the web-search tool plus the analysis call
with a forced `submit_analysis` / `submit_eod` tool. Turning off the gamma briefing
push (2026-09-14) stopped the messages, not the calls: the briefings still feed the
Dealer Positioning page and the NeuralStrike market reports. The operator asked for
the same information without the API spend.

## Decision

Run both phases through the **Claude Code CLI on the subscription** (`claude -p`
with `CLAUDE_CODE_OAUTH_TOKEN`), keeping the API key as a per-call fallback. Scope is
the **scheduled briefings only**; the ad-hoc Analyze button, the Desk market summary
and the autonomous driver stay on the API.

## Why this shape

Every Claude call in a briefing is `client.messages.create(**kwargs)` on a client
`compute` is handed. So the change is a client, not a rewrite: `CliMessagesClient`
implements `messages.create` over the CLI and returns objects with the same
`content` / `stop_reason` shape, and nothing in the prompts, parsing,
code-authoritative overrides (expected move, levels, movers) or rendering changes.
`gamma_analyze` and `eod_briefing` gained an optional `news_client` so the news phase
can take the same client; with `None` it behaves exactly as before.

Mapping, every flag probed live on Claude Code 2.1.270 before building:

| API request | CLI |
|---|---|
| `system` | `--system-prompt-file` (replaces Claude Code's own system prompt) |
| forced tool + `input_schema` | `--tools "" --json-schema <schema>` → `result.structured_output` |
| `web_search` server tool | `--tools WebSearch --allowedTools WebSearch` → `result.result` |
| `output_config.effort` | `--effort` |
| isolation | temp cwd, `--setting-sources ""`, `--strict-mcp-config`, `--no-session-persistence`, `--permission-mode dontAsk` |

## Traps designed out

1. **Silent API billing.** Claude Code prefers `ANTHROPIC_API_KEY` when it is set.
   The child environment strips it (and `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`),
   and a run whose init event reports `apiKeySource` other than `"none"` is refused.
   Either alone could leave a "subscription" briefing billing the key.
2. **Memory text as news.** The API path discards the answer when a search fails
   (`_NEWS_ABORT_ERROR_CODES`). The CLI hides error codes, so the rule became: at
   least one `WebSearch` result carrying links, or the call is unusable.
3. **The token in a process listing.** It travels only in the child's environment
   (the stream-key lesson).
4. **A counter that stops meaning anything.** `_count_anthropic_call(client)` skips a
   client declaring `bills_api_per_call = False`; `FallbackClient` counts the call
   itself when it falls back. Settings → API usage stays a count of billed calls,
   which is the evidence the move saves anything.
5. **Tests spending the subscription.** The options_svc conftest makes the real
   subprocess runner raise; every test injects its own.

## Failure behaviour

Any CLI failure (non-zero exit, timeout at 360 s, no result, error subtype, missing
structured output, no successful search, API-key billing) raises `CliUnusable`;
`FallbackClient` records a degrade (`options.briefing_claude_code`, visible on the
Status page's options card) and repeats the same request on the API key. With no key
it raises, and the briefing renders its existing failure page. When the CLI binary or
token file is absent, `make_briefing_client` returns `None` and the briefings call
exactly as they always did — every machine but the prod box.

## Verified

- options_svc suite green (1860), including 25 new client tests and 4 wiring tests.
- Live on vps2 against prod's compute, read-only (nothing cached, persisted or
  pushed): an intraday briefing (73 s, 6 news lines, 3 indices) and an EOD briefing
  (52 s, with `next_session`), **4 subscription calls, 0 API fallbacks**; the rendered
  intraday page matched the normal briefing layout.

## Not built

- The ad-hoc Analyze button (interactive latency matters more there), the Desk
  summary and the driver decider. The same client fits all three; the driver should
  wait for a track record here, since a slow or missed decision costs more than a
  late briefing.
- `max_tokens` is not mapped: the CLI has no per-call output cap, which removes the
  truncation failure the API path logs rather than reproducing it.
