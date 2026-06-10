"""Unit tests for the search command workflow."""

import io

import pytest
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import ui
from main import format_count
from nextgen.pipeline import SearchResult
from nextgen.providers import ProviderConfigError


def test_search_sets_last_results(monkeypatch, mock_cli):
    results = [
        SearchResult(
            track_id="artist 1|||track a",
            song="Track A",
            artist="Artist 1",
            year="2020",
            score=0.9,
            strict_ratio=0.8,
            sources=["source-1"],
            providers=["codex"],
        )
    ]

    def fake_run(self, query, expanded=False, progress=None, on_result=None):
        return results, "run-1"

    monkeypatch.setattr(mock_cli, "_search_pipeline", None, raising=False)
    monkeypatch.setattr(
        mock_cli.__class__,
        "search_pipeline",
        property(lambda self: type("P", (), {"run": fake_run})()),
    )

    mock_cli.search_songs("late night jazz")

    assert mock_cli.last_search_query == "late night jazz"
    assert mock_cli.last_search_results[0]["song"] == "Track A"
    assert mock_cli.last_search_results[0]["track_id"] == "artist 1|||track a"


def test_search_resets_state_on_error(monkeypatch, mock_cli):
    """When the search pipeline raises, ALL search state fields must be cleared."""
    # Set up some stale state from a previous search
    mock_cli.last_search_query = "previous query"
    mock_cli.last_search_results = [{"song": "Stale"}]
    mock_cli.last_search_run_id = "old-run"
    mock_cli.last_search_track_ids = ["old-track"]
    mock_cli.last_search_summary = "old summary"
    mock_cli.last_search_metrics = ["metric"]
    mock_cli.last_search_constraints = {"key": "val"}
    mock_cli.last_search_expanded = True
    mock_cli.last_search_policy = {"tier": 1}
    mock_cli.last_search_cached = True

    class FailPipeline:
        def run(self, query, expanded=False, progress=None, on_result=None):
            raise RuntimeError("search failed")

    monkeypatch.setattr(mock_cli, "_search_pipeline", None, raising=False)
    monkeypatch.setattr(
        mock_cli.__class__, "search_pipeline", property(lambda self: FailPipeline())
    )

    mock_cli.search_songs("new query")

    # All state fields should be reset
    assert mock_cli.last_search_results is None
    assert mock_cli.last_search_query is None
    assert mock_cli.last_search_summary is None
    assert mock_cli.last_search_metrics is None
    assert mock_cli.last_search_constraints is None
    assert mock_cli.last_search_expanded is False
    assert mock_cli.last_search_policy is None
    assert mock_cli.last_search_run_id is None
    assert mock_cli.last_search_track_ids is None
    assert mock_cli.last_search_cached is False


def test_search_resets_state_on_empty_results(monkeypatch, mock_cli):
    """When pipeline returns empty results, search state should be fully reset."""
    mock_cli.last_search_results = [{"song": "Stale"}]
    mock_cli.last_search_run_id = "old-run"

    def fake_run(self, query, expanded=False, progress=None, on_result=None):
        return [], "run-empty"

    monkeypatch.setattr(mock_cli, "_search_pipeline", None, raising=False)
    monkeypatch.setattr(
        mock_cli.__class__,
        "search_pipeline",
        property(lambda self: type("P", (), {"run": fake_run})()),
    )

    mock_cli.search_songs("empty results query")

    assert mock_cli.last_search_results is None
    assert mock_cli.last_search_run_id is None


def test_search_with_list_query(monkeypatch, mock_cli):
    """search_songs should join list queries into a string."""
    results = [
        SearchResult(
            track_id="a|||b",
            song="B",
            artist="A",
            year="2023",
            score=0.5,
            strict_ratio=0.5,
            sources=[],
            providers=["codex"],
        )
    ]

    def fake_run(self, query, expanded=False, progress=None, on_result=None):
        return results, "run-list"

    monkeypatch.setattr(mock_cli, "_search_pipeline", None, raising=False)
    monkeypatch.setattr(
        mock_cli.__class__,
        "search_pipeline",
        property(lambda self: type("P", (), {"run": fake_run})()),
    )

    mock_cli.search_songs(["indie", "rock", "2023"])

    assert mock_cli.last_search_query == "indie rock 2023"


def _fake_pipeline(results, **attrs):
    """A stand-in search_pipeline exposing run() + the run-level scalar attrs
    that search_songs reads via getattr (last_cached/last_summary/...)."""
    defaults = {
        "last_cached": False,
        "last_summary": None,
        "last_constraints": {},
        "last_requested_metrics": [],
    }
    defaults.update(attrs)

    def run(self, query, expanded=False, progress=None, on_result=None):
        return results, "run-x"

    return type("P", (), {**defaults, "run": run})


def test_search_applies_monthly_listener_constraint(monkeypatch, mock_cli):
    """The '<10k monthly listeners' constraint drops over-limit rows on the
    display path, and the summary/metrics/constraints state is populated."""
    results = [
        SearchResult(
            track_id="a|||over",
            song="Over",
            artist="A",
            year="2020",
            score=0.9,
            strict_ratio=0.8,
            sources=[],
            providers=["claude"],
            metrics={"monthly_listeners": 50000},
        ),
        SearchResult(
            track_id="b|||under",
            song="Under",
            artist="B",
            year="2021",
            score=0.8,
            strict_ratio=0.8,
            sources=[],
            providers=["claude"],
            metrics={"monthly_listeners": 5000},
        ),
    ]
    fake = _fake_pipeline(
        results,
        last_summary="why these",
        last_constraints={"max_monthly_listeners": 10000},
        last_requested_metrics=["monthly_listeners"],
    )
    monkeypatch.setattr(mock_cli, "_search_pipeline", None, raising=False)
    monkeypatch.setattr(mock_cli.__class__, "search_pipeline", property(lambda self: fake()))

    mock_cli.search_songs("songs under 10000 monthly listeners")

    assert [r["song"] for r in mock_cli.last_search_results] == ["Under"]
    assert mock_cli.last_search_track_ids == ["b|||under"]
    assert mock_cli.last_search_summary == "why these"
    assert mock_cli.last_search_metrics == ["monthly_listeners"]
    assert mock_cli.last_search_constraints == {"max_monthly_listeners": 10000}


def test_search_keeps_unverified_rows_when_metrics_absent(monkeypatch, mock_cli):
    """Lenient policy: a constrained query keeps rows that carry no metric to
    verify, rather than nuking a sparse provider response to empty."""
    results = [
        SearchResult(
            track_id="a|||x",
            song="X",
            artist="A",
            year="2020",
            score=0.9,
            strict_ratio=0.8,
            sources=[],
            providers=["claude"],
            metrics={},
        ),
        SearchResult(
            track_id="b|||y",
            song="Y",
            artist="B",
            year="2021",
            score=0.8,
            strict_ratio=0.8,
            sources=[],
            providers=["claude"],
            metrics={},
        ),
    ]
    fake = _fake_pipeline(
        results,
        last_constraints={"max_monthly_listeners": 10000},
        last_requested_metrics=["monthly_listeners"],
    )
    monkeypatch.setattr(mock_cli, "_search_pipeline", None, raising=False)
    monkeypatch.setattr(mock_cli.__class__, "search_pipeline", property(lambda self: fake()))

    mock_cli.search_songs("songs under 10000 monthly listeners")

    assert {r["song"] for r in mock_cli.last_search_results} == {"X", "Y"}


@pytest.fixture
def ui_sink():
    """Capture everything routed through ui._emit; always reset afterwards."""
    captured = []
    ui.set_output_sink(captured.append)
    yield captured
    ui.set_output_sink(None)


def _install_pipeline(monkeypatch, mock_cli, pipeline_factory):
    monkeypatch.setattr(mock_cli, "_search_pipeline", None, raising=False)
    monkeypatch.setattr(
        mock_cli.__class__, "search_pipeline", property(lambda self: pipeline_factory())
    )


def _render(renderable) -> str:
    buf = io.StringIO()
    Console(file=buf, width=120, force_terminal=False).print(renderable)
    return buf.getvalue()


class TestSearchFailureRendering:
    def test_provider_config_error_renders_actionable_panel(self, monkeypatch, mock_cli, ui_sink):
        class Fail:
            def run(self, query, expanded=False, progress=None, on_result=None):
                raise ProviderConfigError("No search providers configured.")

        _install_pipeline(monkeypatch, mock_cli, Fail)

        mock_cli.search_songs("anything")

        panels = [r for r in ui_sink if isinstance(r, Panel)]
        assert panels, "expected an error panel on the output sink"
        panel = panels[-1]
        assert panel.title == "Search unavailable"
        assert panel.border_style == "red"
        text = panel.renderable.plain
        assert "No search providers configured." in text
        assert "config/.env" in text
        assert "/env" in text
        # The failure still resets the search state.
        assert mock_cli.last_search_results is None

    def test_generic_failure_renders_red_panel_not_yellow_text(
        self, monkeypatch, mock_cli, ui_sink
    ):
        class Fail:
            def run(self, query, expanded=False, progress=None, on_result=None):
                raise RuntimeError("provider exploded")

        _install_pipeline(monkeypatch, mock_cli, Fail)

        mock_cli.search_songs("anything")

        panels = [r for r in ui_sink if isinstance(r, Panel)]
        assert any(
            p.border_style == "red" and "provider exploded" in p.renderable.plain for p in panels
        ), "expected the failure as a red panel"
        yellow = [r for r in ui_sink if isinstance(r, Text) and r.style == "yellow"]
        assert not any("provider exploded" in t.plain for t in yellow), (
            "failure must not be downgraded to a yellow warning"
        )


class TestFormatCount:
    def test_thousands(self):
        assert format_count(8200) == "8.2k"

    def test_trailing_zero_trimmed(self):
        assert format_count(10000) == "10k"

    def test_millions(self):
        assert format_count(1_500_000) == "1.5M"

    def test_small_numbers_untouched(self):
        assert format_count(950) == "950"

    def test_fractional_small_number(self):
        assert format_count(0.85) == "0.85"


class TestMetricCell:
    def test_missing_metric_is_dim_dash(self, mock_cli):
        cell = mock_cli._metric_cell({}, "monthly_listeners", {"max_monthly_listeners": 10000})
        assert cell.plain == "—"
        assert cell.style == "dim"

    def test_unparseable_metric_is_dim_dash(self, mock_cli):
        cell = mock_cli._metric_cell({"monthly_listeners": "unknown"}, "monthly_listeners", {})
        assert cell.plain == "—"
        assert cell.style == "dim"

    def test_bound_satisfied_is_green(self, mock_cli):
        cell = mock_cli._metric_cell(
            {"monthly_listeners": 8200}, "monthly_listeners", {"max_monthly_listeners": 10000}
        )
        assert cell.plain == "8.2k"
        assert cell.style == "green"

    def test_bound_violated_is_plain(self, mock_cli):
        cell = mock_cli._metric_cell(
            {"monthly_listeners": 50000}, "monthly_listeners", {"max_monthly_listeners": 10000}
        )
        assert cell.plain == "50k"
        assert cell.style != "green"

    def test_no_bound_is_plain(self, mock_cli):
        cell = mock_cli._metric_cell({"monthly_listeners": 8200}, "monthly_listeners", {})
        assert cell.plain == "8.2k"
        assert cell.style != "green"

    def test_min_bound_mirrors_filter(self, mock_cli):
        kept = mock_cli._metric_cell(
            {"monthly_listeners": "12k"}, "monthly_listeners", {"min_monthly_listeners": 10000}
        )
        dropped = mock_cli._metric_cell(
            {"monthly_listeners": "9k"}, "monthly_listeners", {"min_monthly_listeners": 10000}
        )
        assert kept.style == "green"
        assert dropped.style != "green"

    def test_similarity_percentage_normalized_like_filter(self, mock_cli):
        # 85 -> 0.85 (the 1<sim<=100 -> /100 rule); default floor 0.55 -> green.
        cell = mock_cli._metric_cell(
            {"similarity": 85}, "similarity", {"similarity_requested": True}
        )
        assert cell.plain == "0.85"
        assert cell.style == "green"

    def test_similarity_below_floor_is_plain(self, mock_cli):
        cell = mock_cli._metric_cell(
            {"similarity": 0.4}, "similarity", {"similarity_requested": True}
        )
        assert cell.plain == "0.40"
        assert cell.style != "green"

    def test_similarity_not_requested_is_plain(self, mock_cli):
        cell = mock_cli._metric_cell({"similarity": 0.9}, "similarity", {})
        assert cell.plain == "0.90"
        assert cell.style != "green"


class TestSearchResultsPersistenceAndMetricColumns:
    def _result(self):
        return SearchResult(
            track_id="a|||obscure",
            song="Obscure",
            artist="A",
            year="2024",
            score=0.9,
            strict_ratio=0.8,
            sources=["s1"],
            providers=["claude"],
            metrics={"monthly_listeners": 8200},
        )

    def _clean_env(self, monkeypatch):
        for var in (
            "SEARCH_STREAM_FULL",
            "SEARCH_FINAL_TABLE_MODE",
            "SEARCH_LIVE_MODE",
            "SEARCH_SIMILARITY_MIN",
        ):
            monkeypatch.delenv(var, raising=False)

    def test_interactive_search_persists_compact_table_with_metrics(
        self, monkeypatch, mock_cli, ui_sink
    ):
        """TUI mode: the final compact table must reach the OUTPUT sink (the
        scrollback), not only the transient preview — and carry the requested
        metric column the constraint filter ran on."""
        monkeypatch.setenv("TUNR_INTERACTIVE", "1")
        self._clean_env(monkeypatch)
        fake = _fake_pipeline(
            [self._result()],
            last_requested_metrics=["monthly_listeners"],
            last_constraints={"max_monthly_listeners": 10000},
        )
        _install_pipeline(monkeypatch, mock_cli, fake)
        previews = []
        ui.set_preview_sink(previews.append)
        try:
            mock_cli.search_songs("obscure jams under 10k monthly listeners")
        finally:
            ui.set_preview_sink(None)

        tables = [r for r in ui_sink if isinstance(r, Table)]
        assert tables, "no final table reached the scrollback output sink"
        final = tables[-1]
        rendered = _render(final)
        assert "Listeners" in rendered
        assert "8.2k" in rendered
        assert "Obscure" in rendered
        # Compact projection + the metric column, satisfied bound in green.
        headers = [col.header for col in final.columns]
        assert headers == ["#", "Song", "Artist", "Score", "Strict", "Status", "Listeners"]
        listener_cell = list(final.columns[-1].cells)[0]
        assert isinstance(listener_cell, Text)
        assert listener_cell.style == "green"

    def test_noninteractive_full_table_includes_metric_column(self, monkeypatch, mock_cli, ui_sink):
        monkeypatch.delenv("TUNR_INTERACTIVE", raising=False)
        self._clean_env(monkeypatch)
        fake = _fake_pipeline(
            [self._result()],
            last_requested_metrics=["monthly_listeners"],
            last_constraints={"max_monthly_listeners": 10000},
        )
        _install_pipeline(monkeypatch, mock_cli, fake)

        mock_cli.search_songs("obscure jams under 10k monthly listeners")

        tables = [r for r in ui_sink if isinstance(r, Table)]
        assert tables
        rendered = _render(tables[-1])
        assert "Listeners" in rendered
        assert "8.2k" in rendered

    def test_final_table_mode_none_remains_escape_hatch(self, monkeypatch, mock_cli, ui_sink):
        monkeypatch.setenv("TUNR_INTERACTIVE", "1")
        self._clean_env(monkeypatch)
        monkeypatch.setenv("SEARCH_FINAL_TABLE_MODE", "none")
        fake = _fake_pipeline([self._result()])
        _install_pipeline(monkeypatch, mock_cli, fake)

        mock_cli.search_songs("anything")

        assert not [r for r in ui_sink if isinstance(r, Table)]


def _sr(track_id, metrics):
    return SearchResult(
        track_id=track_id,
        song=track_id,
        artist="A",
        year="2020",
        score=0.5,
        strict_ratio=0.5,
        sources=[],
        providers=["claude"],
        metrics=metrics,
    )


class TestApplyMetricConstraints:
    """Unit tests for the pure display-path filter + its stats accounting."""

    def test_missing_listener_metric_but_similarity_fail_is_dropped_not_unverified(self, mock_cli):
        # Row lacks monthly_listeners (would be 'unverified') AND fails similarity
        # (0.5 < 0.55 default) -> must count as dropped ONCE, never also unverified.
        rows = [_sr("a", {"similarity": 0.5})]
        kept, stats = mock_cli._apply_metric_constraints(
            rows, {"max_monthly_listeners": 10000, "similarity_requested": True}
        )
        assert kept == []
        assert stats == {"kept": 0, "dropped": 1, "unverified": 0}

    def test_missing_similarity_metric_kept_and_flagged_unverified(self, mock_cli):
        # Passes the listener bound, but the similarity metric is absent -> kept,
        # and counted unverified (symmetric with the monthly_listeners branch).
        rows = [_sr("a", {"monthly_listeners": 5000})]
        kept, stats = mock_cli._apply_metric_constraints(
            rows, {"max_monthly_listeners": 10000, "similarity_requested": True}
        )
        assert [r.track_id for r in kept] == ["a"]
        assert stats == {"kept": 1, "dropped": 0, "unverified": 1}

    def test_zero_bound_is_honoured(self, mock_cli):
        # max_monthly_listeners=0 is a real (if odd) bound, not "no constraint".
        rows = [_sr("a", {"monthly_listeners": 5})]
        kept, stats = mock_cli._apply_metric_constraints(rows, {"max_monthly_listeners": 0})
        assert kept == []
        assert stats["dropped"] == 1

    def test_no_constraints_passthrough(self, mock_cli):
        rows = [_sr("a", {}), _sr("b", {})]
        kept, stats = mock_cli._apply_metric_constraints(rows, {})
        assert kept == rows
        assert stats == {"kept": 0, "dropped": 0, "unverified": 0}


class TestParseMetricNumberNonFinite:
    """NaN/Infinity must read as missing: json.loads accepts bare NaN/Infinity
    literals and float() parses "nan", which used to poison the constraint
    filter and crash format_count's int() conversion."""

    @pytest.mark.parametrize(
        "value", [float("nan"), float("inf"), float("-inf"), "nan", "NaN", "inf", "1e999"]
    )
    def test_non_finite_is_none(self, mock_cli, value):
        assert mock_cli._parse_metric_number(value) is None

    def test_metric_cell_renders_dash_for_nan(self, mock_cli):
        cell = mock_cli._metric_cell({"monthly_listeners": float("nan")}, "monthly_listeners", {})
        assert cell.plain == "—"
