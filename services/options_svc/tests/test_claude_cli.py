"""The Claude Code CLI client behind the scheduled gamma briefings.

Every test injects its own ``run`` — the conftest makes the real subprocess
runner raise, so nothing here can reach the CLI or the subscription.
"""
import json
import subprocess
from types import SimpleNamespace

import pytest

import repo_paths
from services.options_svc import claude_cli, compute


# ── canned stream-json transcripts, in the shape probed live on vps2 (2.1.270) ──

def _init(api_key_source="none"):
    return {"type": "system", "subtype": "init", "apiKeySource": api_key_source,
            "model": "claude-sonnet-5", "tools": []}


def _result(**kw):
    base = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 2,
            "duration_ms": 4000, "total_cost_usd": 0.04, "result": ""}
    base.update(kw)
    return base


def _search(tool_id, ok=True, is_error=None):
    use = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tool_id, "name": "WebSearch", "input": {"query": "q"}}]}}
    body = ('Web search results for query: "q"\n\nLinks: [{"title":"x"}]' if ok
            else "Web search failed: unavailable")
    res = {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tool_id, "content": body, "is_error": is_error}]}}
    return [use, res]


def _stdout(*events):
    return "\n".join(json.dumps(e) for e in events) + "\n"


class _Runner:
    """Records the argv / stdin / env / cwd of each CLI invocation."""

    def __init__(self, stdout="", returncode=0, stderr="", raise_exc=None):
        self.stdout, self.returncode, self.stderr, self.raise_exc = stdout, returncode, stderr, raise_exc
        self.calls = []

    def __call__(self, argv, *, input, env, cwd, timeout):
        system_file = argv[argv.index("--system-prompt-file") + 1]
        with open(system_file, encoding="utf-8") as f:
            system = f.read()
        self.calls.append({"argv": argv, "input": input, "env": env, "cwd": cwd,
                           "timeout": timeout, "system": system})
        if self.raise_exc:
            raise self.raise_exc
        return SimpleNamespace(stdout=self.stdout, stderr=self.stderr, returncode=self.returncode)


def _client(runner, tmp_path, token="tok-abc"):
    tf = tmp_path / "claude.env"
    tf.write_text(f"CLAUDE_CODE_OAUTH_TOKEN={token}\n", encoding="utf-8")
    return claude_cli.CliMessagesClient(cli_path="/usr/bin/true", token_file=str(tf), run=runner)


_TOOL = {"name": "submit_analysis", "description": "d",
         "input_schema": {"type": "object", "properties": {"bias": {"type": "integer"}},
                          "required": ["bias"]}}


def _structured_kwargs():
    return dict(model="claude-sonnet-5", max_tokens=2600, thinking={"type": "disabled"},
                system="You are the analyst.", tools=[_TOOL],
                tool_choice={"type": "tool", "name": "submit_analysis"},
                messages=[{"role": "user", "content": "the prompt"}])


def _news_kwargs():
    return dict(model="claude-sonnet-5", max_tokens=1600, output_config={"effort": "low"},
                system="Search, then list drivers.", tools=[compute._WEB_SEARCH_TOOL],
                messages=[{"role": "user", "content": "What news drove the tape today?"}])


# ── forced tool → structured output ──

def test_forced_tool_call_maps_to_json_schema_and_returns_a_tool_use_block(tmp_path):
    runner = _Runner(_stdout(_init(), _result(structured_output={"bias": 2})))
    resp = _client(runner, tmp_path).messages.create(**_structured_kwargs())

    call = runner.calls[0]
    argv = call["argv"]
    assert json.loads(argv[argv.index("--json-schema") + 1]) == _TOOL["input_schema"]
    assert argv[argv.index("--tools") + 1] == ""               # no built-in tools at all
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5"
    assert call["input"] == "the prompt"
    assert call["system"] == "You are the analyst."
    blocks = resp.content
    assert [(b.type, b.name, b.input) for b in blocks] == [("tool_use", "submit_analysis", {"bias": 2})]


def test_the_briefing_reads_the_cli_reply_exactly_as_it_reads_the_api_reply(tmp_path):
    """compute's own loop over resp.content must find the tool input."""
    runner = _Runner(_stdout(_init(), _result(structured_output={"bias": -1})))
    resp = _client(runner, tmp_path).messages.create(**_structured_kwargs())
    found = None
    for b in (getattr(resp, "content", None) or []):
        if getattr(b, "type", None) == "tool_use" and getattr(b, "name", "") == "submit_analysis":
            found = getattr(b, "input", None)
    assert found == {"bias": -1}
    assert getattr(resp, "stop_reason", None) != "max_tokens"


def test_missing_structured_output_is_unusable(tmp_path):
    runner = _Runner(_stdout(_init(), _result(result="I could not do that")))
    with pytest.raises(claude_cli.CliUnusable):
        _client(runner, tmp_path).messages.create(**_structured_kwargs())


# ── web search → text ──

def test_news_call_enables_only_web_search_and_returns_the_text(tmp_path):
    runner = _Runner(_stdout(_init(), *_search("t1"), _result(result="- Oil jumped.\n- Chips fell.")))
    resp = _client(runner, tmp_path).messages.create(**_news_kwargs())

    argv = runner.calls[0]["argv"]
    assert argv[argv.index("--tools") + 1] == "WebSearch"
    assert argv[argv.index("--allowedTools") + 1] == "WebSearch"
    assert argv[argv.index("--effort") + 1] == "low"
    assert [(b.type, b.text) for b in resp.content] == [("text", "- Oil jumped.\n- Chips fell.")]
    # The real parser downstream sees ordinary driver lines.
    assert compute._extract_driver_lines(resp.content[0].text) == ["Oil jumped.", "Chips fell."]


def test_news_answer_without_a_successful_search_is_unusable(tmp_path):
    """The API path discards memory-text when the search failed; here we cannot see
    error codes, so NO successful search at all means the text is not today's news."""
    for events in ([_init(), _result(result="From memory: stocks rose.")],
                   [_init(), *_search("t1", ok=False, is_error=True), _result(result="stocks rose")]):
        runner = _Runner(_stdout(*events))
        with pytest.raises(claude_cli.CliUnusable, match="search"):
            _client(runner, tmp_path).messages.create(**_news_kwargs())


def test_one_failed_and_one_good_search_keeps_the_answer(tmp_path):
    runner = _Runner(_stdout(_init(), *_search("t1", ok=False, is_error=True), *_search("t2"),
                             _result(result="Fed held.")))
    resp = _client(runner, tmp_path).messages.create(**_news_kwargs())
    assert resp.content[0].text == "Fed held."


# ── billing and credentials: the reason this module exists ──

def test_a_run_billed_to_an_api_key_is_refused(tmp_path):
    runner = _Runner(_stdout(_init(api_key_source="ANTHROPIC_API_KEY"),
                             _result(structured_output={"bias": 1})))
    with pytest.raises(claude_cli.CliUnusable, match="API key"):
        _client(runner, tmp_path).messages.create(**_structured_kwargs())


def test_child_env_strips_api_credentials_and_carries_the_token_only_in_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-should-not-pass")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "also-not")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://elsewhere")
    runner = _Runner(_stdout(_init(), _result(structured_output={"bias": 0})))
    _client(runner, tmp_path, token="tok-xyz").messages.create(**_structured_kwargs())

    call = runner.calls[0]
    assert "ANTHROPIC_API_KEY" not in call["env"]
    assert "ANTHROPIC_AUTH_TOKEN" not in call["env"]
    assert "ANTHROPIC_BASE_URL" not in call["env"]
    assert call["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "tok-xyz"
    assert not any("tok-xyz" in a for a in call["argv"])       # never in the process list


def test_runs_isolated_from_any_project_or_user_settings(tmp_path):
    runner = _Runner(_stdout(_init(), _result(structured_output={"bias": 0})))
    _client(runner, tmp_path).messages.create(**_structured_kwargs())
    call = runner.calls[0]
    argv = call["argv"]
    assert argv[argv.index("--setting-sources") + 1] == ""
    for flag in ("--strict-mcp-config", "--no-session-persistence", "-p"):
        assert flag in argv
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "ns-briefing-" in call["cwd"]                        # a scratch dir, no CLAUDE.md
    assert str(repo_paths.REPO_ROOT) not in call["cwd"]


@pytest.mark.parametrize("runner", [
    _Runner(returncode=1, stderr="boom"),
    _Runner(raise_exc=subprocess.TimeoutExpired(cmd="claude", timeout=1)),
    _Runner(raise_exc=FileNotFoundError("no claude")),
    _Runner(_stdout(_init())),                                    # no result event
    _Runner(_stdout(_init(), _result(subtype="error_max_turns", is_error=True))),
])
def test_every_cli_failure_is_unusable(runner, tmp_path):
    with pytest.raises(claude_cli.CliUnusable):
        _client(runner, tmp_path).messages.create(**_structured_kwargs())


def test_a_request_shape_the_cli_cannot_express_is_unusable(tmp_path):
    runner = _Runner(_stdout(_init(), _result(result="x")))
    kw = _structured_kwargs()
    kw["messages"] = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
                      {"role": "user", "content": "c"}]
    with pytest.raises(claude_cli.CliUnusable):
        _client(runner, tmp_path).messages.create(**kw)
    assert runner.calls == []


def test_missing_token_is_unusable_without_running_anything(tmp_path):
    runner = _Runner(_stdout(_init(), _result(structured_output={"bias": 0})))
    c = claude_cli.CliMessagesClient(cli_path="/usr/bin/true", token_file=str(tmp_path / "absent"),
                                     run=runner)
    with pytest.raises(claude_cli.CliUnusable, match="token"):
        c.messages.create(**_structured_kwargs())
    assert runner.calls == []


# ── fallback to the API, and the call counter ──

class _ApiClient:
    def __init__(self):
        self.calls = []
        outer = self

        class _M:
            def create(self, **kw):
                outer.calls.append(kw)
                return SimpleNamespace(content=[SimpleNamespace(type="text", text="api")])
        self.messages = _M()


def test_cli_success_does_not_touch_the_api_or_the_api_call_counter(tmp_path):
    api, counted = _ApiClient(), []
    runner = _Runner(_stdout(_init(), _result(structured_output={"bias": 3})))
    fb = claude_cli.FallbackClient(_client(runner, tmp_path), api_factory=lambda: api,
                                   count_api_call=lambda: counted.append(1))
    resp = fb.messages.create(**_structured_kwargs())
    assert resp.content[0].input == {"bias": 3}
    assert api.calls == [] and counted == []


def test_cli_failure_falls_back_to_the_api_with_the_same_request_and_counts_it(tmp_path):
    api, counted = _ApiClient(), []
    runner = _Runner(returncode=1, stderr="rate limited")
    fb = claude_cli.FallbackClient(_client(runner, tmp_path), api_factory=lambda: api,
                                   count_api_call=lambda: counted.append(1))
    kw = _structured_kwargs()
    resp = fb.messages.create(**kw)
    assert resp.content[0].text == "api"
    assert api.calls == [kw]
    assert counted == [1]


def test_cli_failure_with_no_api_key_raises_so_the_briefing_renders_its_failure_page(tmp_path):
    runner = _Runner(returncode=1)
    fb = claude_cli.FallbackClient(_client(runner, tmp_path), api_factory=lambda: None,
                                   count_api_call=lambda: None)
    with pytest.raises(RuntimeError):
        fb.messages.create(**_structured_kwargs())


def test_compute_counter_skips_a_client_that_is_not_billed_per_call(monkeypatch):
    recorded = []
    import shared.anthropic_counter as counter
    monkeypatch.setattr(counter, "record", lambda *a, **k: recorded.append(1))
    compute._count_anthropic_call(SimpleNamespace(bills_api_per_call=False))
    assert recorded == []
    compute._count_anthropic_call(SimpleNamespace())             # an ordinary API client
    compute._count_anthropic_call()
    assert recorded == [1, 1]


# ── the factory: when the CLI path is used at all ──

def _make(tmp_path, monkeypatch, *, engine=None, token=True, binary=True):
    cli = tmp_path / "claude"
    if binary:
        cli.write_text("#!/bin/sh\n", encoding="utf-8")
        cli.chmod(0o755)
    tf = tmp_path / "claude.env"
    if token:
        tf.write_text("CLAUDE_CODE_OAUTH_TOKEN=tok\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CLI_PATH", str(cli))
    monkeypatch.setenv("CLAUDE_CLI_TOKEN_FILE", str(tf))
    if engine is None:
        monkeypatch.delenv("BRIEFING_ENGINE", raising=False)
    else:
        monkeypatch.setenv("BRIEFING_ENGINE", engine)
    monkeypatch.setattr(claude_cli, "_is_executable", lambda p: binary and p == str(cli))
    return claude_cli.make_briefing_client(api_factory=lambda: None, count_api_call=lambda: None)


def test_factory_builds_the_cli_client_by_default_when_the_cli_is_installed(tmp_path, monkeypatch):
    monkeypatch.setitem(repo_paths.ENV_FLAGS, "allow_claude", True)
    c = _make(tmp_path, monkeypatch)
    assert isinstance(c, claude_cli.FallbackClient)
    assert c.bills_api_per_call is False


@pytest.mark.parametrize("kw", [{"engine": "api"}, {"token": False}, {"binary": False}])
def test_factory_returns_none_so_the_briefing_uses_the_api_as_before(kw, tmp_path, monkeypatch):
    monkeypatch.setitem(repo_paths.ENV_FLAGS, "allow_claude", True)
    assert _make(tmp_path, monkeypatch, **kw) is None


def test_factory_honours_the_environment_claude_suppression(tmp_path, monkeypatch):
    monkeypatch.setitem(repo_paths.ENV_FLAGS, "allow_claude", False)
    assert _make(tmp_path, monkeypatch) is None
