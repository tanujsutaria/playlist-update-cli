"""Unit tests for anthropic_web_search_wrapper helpers (no network).

The max-tokens default matters: a small cap silently truncates the pipeline's
structured-JSON answer mid-object, so it fails to parse and the wrapper returns
zero results. The default is 4096 (not 1024).
"""

from __future__ import annotations

from anthropic_web_search_wrapper import DEFAULT_WEB_SEARCH_MAX_TOKENS, _resolve_max_tokens


class TestResolveMaxTokens:
    def test_default_is_4096(self):
        assert DEFAULT_WEB_SEARCH_MAX_TOKENS == 4096
        assert _resolve_max_tokens(env={}) == 4096

    def test_env_override(self):
        assert _resolve_max_tokens(env={"ANTHROPIC_WEB_SEARCH_MAX_TOKENS": "8000"}) == 8000

    def test_bad_value_falls_back_to_default(self):
        assert _resolve_max_tokens(env={"ANTHROPIC_WEB_SEARCH_MAX_TOKENS": "not-an-int"}) == 4096
