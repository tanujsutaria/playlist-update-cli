"""Unit tests for the pure ghost-text completion logic (src/completions.py)."""

import asyncio

import pytest

from completions import TunrSuggester, complete_line

COMMANDS = ["update", "stats", "search", "help", "dash"]
FLAGS = {
    "update": ["-h", "--help", "--count", "--fresh-days", "--dry-run"],
    "search": ["-h", "--help", "--limit", "--fresh"],
}
HISTORY = [
    "/stats",
    '/update "My Playlist" --count 10',
    "/search chill beats --limit 5",
]


def _complete(value, commands=COMMANDS, flags=FLAGS, history=HISTORY):
    return complete_line(value, commands=commands, flags=flags, history=history)


class TestCommandCompletion:
    def test_slash_prefix_completes_command(self):
        assert _complete("/up") == "/update"

    def test_bare_prefix_completes_command(self):
        assert _complete("up") == "update"

    def test_first_match_wins_in_inventory_order(self):
        assert _complete("/s") == "/stats"

    def test_exact_command_falls_through_to_history(self):
        # "/update" is already complete -> the history line extends it.
        assert _complete("/update") == '/update "My Playlist" --count 10'

    def test_no_match_no_history_returns_none(self):
        assert _complete("/zzz", history=[]) is None

    def test_command_completion_beats_history(self):
        history = ["/upgrade-me nonsense"]
        assert _complete("/up", history=history) == "/update"


class TestFlagCompletion:
    def test_flag_completes_for_current_command(self):
        value = '/update "My Playlist" --fr'
        assert _complete(value) == '/update "My Playlist" --fresh-days'

    def test_flags_are_per_command(self):
        assert _complete("/search chill --fr", history=[]) == "/search chill --fresh"

    def test_unknown_command_has_no_flags(self):
        assert _complete("/stats --fr", history=[]) is None

    def test_complete_flag_falls_through_to_history(self):
        history = ["/update x --count 10"]
        assert _complete("/update x --count", history=history) == "/update x --count 10"

    def test_dash_inside_quotes_is_not_flag_completed(self):
        # The literal tail ('-b"') differs from the shlex token ("a -b"):
        # completing a flag there would corrupt the quoted argument.
        assert _complete('/search "a -b"', history=[]) is None


class TestHistoryFallback:
    def test_most_recent_matching_line_wins(self):
        history = ["/stats --json", "/stats --playlist X"]
        assert _complete("/stats ", history=history) == "/stats --playlist X"

    def test_recalled_history_line_never_suggests_itself(self):
        # History navigation writes full recalled lines into the Input, which
        # re-triggers the suggester. Deliberate decision: a recalled line may
        # ghost a longer more recent line, but never itself.
        history = ["/stats"]
        assert _complete("/stats", history=history) is None

    def test_recalled_line_may_ghost_longer_more_recent_line(self):
        history = ["/stats", "/stats --json"]
        assert _complete("/stats", history=history) == "/stats --json"

    def test_mid_argument_text_uses_history(self):
        assert _complete('/update "My') == '/update "My Playlist" --count 10'


class TestRobustness:
    def test_empty_input(self):
        assert _complete("") is None

    def test_whitespace_only_input(self):
        assert _complete("   ") is None

    def test_unbalanced_double_quote_falls_back_to_history(self):
        assert _complete('/update "My Pl') == '/update "My Playlist" --count 10'

    def test_unbalanced_single_quote_no_history_is_none(self):
        assert _complete("/search 'lo-fi", history=[]) is None

    def test_lone_dash_first_token_is_not_a_command(self):
        assert _complete("--", history=[]) is None

    def test_trailing_space_skips_token_completion(self):
        # "/up " is no longer typing the command token; only history applies.
        assert _complete("/up ", history=[]) is None

    @pytest.mark.parametrize(
        "value",
        ["/up", "up", '/update "My Playlist" --fr', '/update "My Pl', "/stats "],
    )
    def test_suggestion_always_extends_exact_value(self, value):
        suggestion = _complete(value)
        if suggestion is not None:
            assert suggestion.startswith(value)
            assert suggestion != value


class TestTunrSuggester:
    @staticmethod
    def _run(coro):
        try:
            return asyncio.run(coro)
        finally:
            # py3.9: asyncio.run() leaves the main thread without a current
            # event loop; restore one for later tests (repo pattern, see
            # tests/test_dashboard.py TestPilotSmoke).
            asyncio.set_event_loop(asyncio.new_event_loop())

    def test_get_suggestion_delegates_to_complete_line(self):
        suggester = TunrSuggester(commands=COMMANDS, flags=FLAGS, history=lambda: HISTORY)
        assert self._run(suggester.get_suggestion("/up")) == "/update"

    def test_history_provider_sees_rebound_list(self):
        # The app REBINDS its history list on truncation; a provider callable
        # keeps the suggester pointed at the live list.
        state = {"history": ["/stats"]}
        suggester = TunrSuggester(commands=COMMANDS, flags=FLAGS, history=lambda: state["history"])
        assert self._run(suggester.get_suggestion("/stats --j")) is None
        state["history"] = ["/stats --json"]  # rebind, as _append_history does
        assert self._run(suggester.get_suggestion("/stats --j")) == "/stats --json"

    def test_cache_disabled_and_case_sensitive(self):
        # Both are load-bearing: caching would serve stale history hits, and
        # case-insensitivity would hand complete_line a casefolded value that
        # breaks the exact-prefix ghost-text invariant.
        suggester = TunrSuggester(commands=COMMANDS, flags=FLAGS, history=lambda: [])
        assert suggester.cache is None
        assert suggester.case_sensitive is True
