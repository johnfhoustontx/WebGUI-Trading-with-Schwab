"""Run the scheduled gamma briefings on the Claude subscription, via the Claude Code CLI.

A briefing is two Claude calls -- the news phase (web search) and the analysis
(a forced ``submit_analysis`` / ``submit_eod`` tool) -- and both reach Claude
through ``client.messages.create(**kwargs)`` on a client ``compute`` is handed.
``CliMessagesClient`` implements that one method by running ``claude -p`` with
the subscription token instead of calling the API with the key, so nothing in
the briefing's prompts, parsing, code-authoritative overrides or rendering
changes. Only the billing does.

The request maps onto the CLI like this (every flag probed live on 2.1.270):

    system                       --system-prompt-file (replaces Claude Code's own)
    a forced tool + its schema   --tools "" --json-schema <input_schema>
                                 -> result.structured_output, returned as a tool_use block
    the web_search server tool   --tools WebSearch --allowedTools WebSearch
                                 -> result.result, returned as a text block
    output_config.effort         --effort
    model                        --model

Guards that exist because each failure would otherwise look like success:

* **The API key is stripped from the child's environment**, and a run whose
  init event reports any ``apiKeySource`` other than ``"none"`` is refused.
  Claude Code prefers ``ANTHROPIC_API_KEY`` when it is set, and options_svc's
  own environment can carry it -- so without both, a "subscription" briefing
  would quietly bill the key while every log line claimed otherwise.
* **A news answer with no successful web search is refused.** The API path
  discards memory-text when a search fails (``_NEWS_ABORT_ERROR_CODES``); the CLI
  hides error codes, so the equivalent rule is that at least one search must
  have come back with results.
* **The token travels only in the environment**, never in argv, where any local
  process listing would print it.

Any failure raises ``CliUnusable``; ``FallbackClient`` catches it, records a
degrade and repeats the SAME request on the API, so a bad day on the
subscription costs money rather than a missing briefing.

Configuration (options_svc's environment; restart the service to change):
    BRIEFING_ENGINE          "claude_code" (default) or "api" to switch the CLI path off
    CLAUDE_CLI_PATH          default ~/.local/bin/claude
    CLAUDE_CLI_TOKEN_FILE    default ~/.config/neuralstrike/claude.env, a 600 file
                             holding CLAUDE_CODE_OAUTH_TOKEN=... (``claude setup-token``)
When the binary or token is absent the factory returns None and the briefings run on
the API exactly as they did before, with no degrade noise -- that is every machine
but the prod box.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from types import SimpleNamespace

from repo_paths import ENV_FLAGS
from services import _degrade

log = logging.getLogger(__name__)

DEFAULT_CLI_PATH = "~/.local/bin/claude"
DEFAULT_TOKEN_FILE = "~/.config/neuralstrike/claude.env"
TOKEN_KEY = "CLAUDE_CODE_OAUTH_TOKEN"
TIMEOUT_SEC = 360
# Credentials Claude Code would prefer over the subscription token, plus the one
# that would point it at another endpoint entirely.
_STRIPPED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")


class CliUnusable(RuntimeError):
    """The CLI could not produce a reply the briefing can use."""


def _default_run(argv, *, input, env, cwd, timeout):
    return subprocess.run(argv, input=input, env=env, cwd=cwd, timeout=timeout,
                          capture_output=True, text=True)


def _is_executable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def _read_token(token_file: str) -> str | None:
    try:
        with open(os.path.expanduser(token_file), encoding="utf-8") as f:
            for line in f:
                if line.startswith(TOKEN_KEY + "="):
                    value = line.split("=", 1)[1].strip()
                    return value or None
    except OSError:
        return None
    return None


def _child_env(token: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _STRIPPED_ENV}
    env[TOKEN_KEY] = token
    return env


def _request_shape(kwargs) -> tuple[str, str, str | None, list]:
    """(mode, prompt, forced tool name, extra argv) or CliUnusable."""
    messages = kwargs.get("messages") or []
    if len(messages) != 1 or messages[0].get("role") != "user" \
            or not isinstance(messages[0].get("content"), str):
        raise CliUnusable("only a single plain-text user turn can be sent to the CLI")
    prompt = messages[0]["content"]
    tools = kwargs.get("tools") or []
    choice = kwargs.get("tool_choice") or {}
    extra = ["--model", str(kwargs.get("model") or "sonnet")]
    effort = (kwargs.get("output_config") or {}).get("effort")
    if effort:
        extra += ["--effort", str(effort)]

    if choice.get("type") == "tool":
        tool = next((t for t in tools if t.get("name") == choice.get("name")), None)
        if not tool or not isinstance(tool.get("input_schema"), dict):
            raise CliUnusable(f"forced tool {choice.get('name')!r} has no schema")
        return "structured", prompt, tool["name"], extra + [
            "--tools", "", "--json-schema", json.dumps(tool["input_schema"])]
    if tools and all(str(t.get("type", "")).startswith("web_search") for t in tools):
        return "search", prompt, None, extra + ["--tools", "WebSearch", "--allowedTools", "WebSearch"]
    if tools:
        raise CliUnusable("tool set the CLI path does not map")
    return "text", prompt, None, extra + ["--tools", ""]


def _events(stdout: str) -> list:
    out = []
    for line in (stdout or "").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _successful_searches(events) -> int:
    searches = {}
    for e in events:
        if e.get("type") == "assistant":
            for c in (e.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") == "WebSearch":
                    searches[c.get("id")] = False
    for e in events:
        if e.get("type") == "user":
            for c in (e.get("message") or {}).get("content") or []:
                if not (isinstance(c, dict) and c.get("type") == "tool_result"
                        and c.get("tool_use_id") in searches) or c.get("is_error"):
                    continue
                body = c.get("content")
                body = body if isinstance(body, str) else json.dumps(body)
                if "Links:" in body:
                    searches[c["tool_use_id"]] = True
    return sum(searches.values())


class CliMessagesClient:
    """``messages.create`` over ``claude -p``. See the module docstring."""

    bills_api_per_call = False

    def __init__(self, cli_path=None, token_file=None, run=None, timeout=TIMEOUT_SEC):
        self.cli_path = os.path.expanduser(cli_path or DEFAULT_CLI_PATH)
        self.token_file = token_file or DEFAULT_TOKEN_FILE
        self._run = run or _default_run
        self.timeout = timeout
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        mode, prompt, tool_name, extra = _request_shape(kwargs)
        token = _read_token(self.token_file)
        if not token:
            raise CliUnusable("no Claude Code token in the token file")

        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="ns-briefing-") as cwd:
            system_file = os.path.join(cwd, "system.txt")
            with open(system_file, "w", encoding="utf-8") as f:
                f.write(str(kwargs.get("system") or ""))
            argv = [self.cli_path, "-p", "--system-prompt-file", system_file,
                    "--setting-sources", "", "--strict-mcp-config", "--no-session-persistence",
                    "--permission-mode", "dontAsk", "--output-format", "stream-json", "--verbose",
                    *extra]
            try:
                proc = self._run(argv, input=prompt, env=_child_env(token), cwd=cwd,
                                 timeout=self.timeout)
            except subprocess.TimeoutExpired as exc:
                raise CliUnusable(f"the CLI run exceeded {self.timeout}s") from exc
            except OSError as exc:
                raise CliUnusable(f"the CLI could not start ({type(exc).__name__})") from exc

        if proc.returncode != 0:
            raise CliUnusable(f"the CLI exited {proc.returncode}: {(proc.stderr or '')[-300:]}")
        events = _events(proc.stdout)
        init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
        source = init.get("apiKeySource")
        if source != "none":
            raise CliUnusable(f"the CLI run would bill an API key (apiKeySource={source!r})")
        result = next((e for e in reversed(events) if e.get("type") == "result"), None)
        if result is None:
            raise CliUnusable("the CLI run ended without a result")
        if result.get("is_error") or result.get("subtype") != "success":
            raise CliUnusable(f"the CLI run ended as {result.get('subtype')}")

        if mode == "structured":
            data = result.get("structured_output")
            if not isinstance(data, dict):
                raise CliUnusable("the CLI run returned no structured output")
            content = [SimpleNamespace(type="tool_use", name=tool_name, input=data)]
            stop = "tool_use"
        else:
            if mode == "search" and _successful_searches(events) == 0:
                raise CliUnusable("no web search came back with results")
            text = (result.get("result") or "").strip()
            if not text:
                raise CliUnusable("the CLI run returned no text")
            content = [SimpleNamespace(type="text", text=text)]
            stop = "end_turn"

        log.info("briefing %s call on the Claude subscription: %.0fs, %s turns "
                 "(API-equivalent $%.3f, not billed)", mode, time.monotonic() - started,
                 result.get("num_turns"), float(result.get("total_cost_usd") or 0))
        return SimpleNamespace(content=content, stop_reason=stop, model=init.get("model"))


class FallbackClient:
    """The CLI first; on any failure, the same request on the API key."""

    bills_api_per_call = False

    def __init__(self, cli, api_factory, count_api_call):
        self._cli = cli
        self._api_factory = api_factory
        self._count = count_api_call
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        try:
            return self._cli.messages.create(**kwargs)
        except Exception as exc:
            _degrade.degraded("options.briefing_claude_code", detail=str(exc)[:200])
        api = self._api_factory()
        if api is None:
            raise RuntimeError("the Claude Code run failed and no API key is configured")
        self._count()                       # this one IS a billed API call
        return api.messages.create(**kwargs)


def make_briefing_client(api_factory, count_api_call):
    """A ``FallbackClient`` for the scheduled briefings, or None to use the API as before."""
    if not ENV_FLAGS.get("allow_claude", True):
        return None
    if os.environ.get("BRIEFING_ENGINE", "claude_code").strip().lower() != "claude_code":
        return None
    cli_path = os.path.expanduser(os.environ.get("CLAUDE_CLI_PATH") or DEFAULT_CLI_PATH)
    token_file = os.environ.get("CLAUDE_CLI_TOKEN_FILE") or DEFAULT_TOKEN_FILE
    if not _is_executable(cli_path) or not _read_token(token_file):
        return None
    return FallbackClient(CliMessagesClient(cli_path=cli_path, token_file=token_file),
                          api_factory=api_factory, count_api_call=count_api_call)
