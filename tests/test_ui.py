"""Unit tests for src/ui.py rendering helpers.

These render via rich to the module console; we assert they run without error
and (where practical) that key substrings reach stdout. A few tests route
through the output/preview sinks to capture renderables directly.
"""

from __future__ import annotations

import pytest
from rich.panel import Panel
from rich.text import Text

import ui


@pytest.fixture
def reset_sinks():
    """Ensure module-global sinks are cleared before and after each test."""
    ui.set_output_sink(None)
    ui.set_preview_sink(None)
    yield
    ui.set_output_sink(None)
    ui.set_preview_sink(None)


class TestStdoutRendering:
    def test_section_prints_title(self, capsys, reset_sinks):
        ui.section("My Section")
        out = capsys.readouterr().out
        assert "My Section" in out

    def test_section_with_subtitle(self, capsys, reset_sinks):
        ui.section("Title", subtitle="extra")
        out = capsys.readouterr().out
        assert "Title" in out
        assert "extra" in out

    def test_subsection_prints(self, capsys, reset_sinks):
        ui.subsection("Sub heading")
        assert "Sub heading" in capsys.readouterr().out

    def test_table_prints_headers_and_rows(self, capsys, reset_sinks):
        ui.table(["Name", "Count"], [["alpha", 1], ["beta", 2]])
        out = capsys.readouterr().out
        assert "Name" in out
        assert "Count" in out
        assert "alpha" in out
        assert "beta" in out

    def test_key_value_table_prints(self, capsys, reset_sinks):
        ui.key_value_table([["Total", 42], ["Status", "ok"]])
        out = capsys.readouterr().out
        assert "Total" in out
        assert "42" in out
        assert "Status" in out

    def test_info_prints_message(self, capsys, reset_sinks):
        ui.info("operation complete")
        assert "operation complete" in capsys.readouterr().out

    def test_warning_prints_message(self, capsys, reset_sinks):
        ui.warning("be careful")
        assert "be careful" in capsys.readouterr().out

    def test_json_output_prints_payload(self, capsys, reset_sinks):
        ui.json_output({"key": "value", "n": 7})
        out = capsys.readouterr().out
        assert "key" in out
        assert "value" in out


class TestError:
    def test_error_emits_red_panel_to_sink(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.error("it broke", title="Search unavailable")
        assert len(captured) == 1
        panel = captured[0]
        assert isinstance(panel, Panel)
        assert panel.border_style == "red"
        assert panel.title == "Search unavailable"
        assert isinstance(panel.renderable, Text)
        assert panel.renderable.plain == "it broke"
        assert panel.renderable.style == "red"

    def test_error_default_title(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.error("boom")
        assert captured[0].title == "Error"

    def test_error_prints_to_console_without_sink(self, capsys, reset_sinks):
        ui.error("plain failure")
        assert "plain failure" in capsys.readouterr().out

    def test_error_json_mode_goes_to_stderr_keeping_stdout_pure(self, capsys, reset_sinks):
        ui.set_json_mode(True)
        try:
            ui.error("fatal but json")
        finally:
            ui.set_json_mode(False)
        out, err = capsys.readouterr()
        assert out == ""
        assert "fatal but json" in err

    def test_error_json_mode_prefers_installed_sink(self, capsys, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.set_json_mode(True)
        try:
            ui.error("sink wins")
        finally:
            ui.set_json_mode(False)
        out, err = capsys.readouterr()
        assert out == "" and err == ""
        assert len(captured) == 1
        assert captured[0].renderable.plain == "sink wins"


class TestNotice:
    def test_notice_is_dim_text_via_emit(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.notice("nothing to do")
        assert len(captured) == 1
        assert isinstance(captured[0], Text)
        assert captured[0].plain == "nothing to do"
        assert captured[0].style == "dim"

    def test_notice_prints_without_sink(self, capsys, reset_sinks):
        ui.notice("empty library")
        assert "empty library" in capsys.readouterr().out

    def test_notice_silenced_in_json_mode(self, capsys, reset_sinks):
        ui.set_json_mode(True)
        try:
            ui.notice("quiet")
        finally:
            ui.set_json_mode(False)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""


class TestTextCellPassthrough:
    def test_table_text_cell_content_reaches_stdout(self, capsys, reset_sinks):
        ui.table(["Listeners"], [[Text("8.2k", style="green")]])
        assert "8.2k" in capsys.readouterr().out

    def test_table_text_cell_preserved_with_style_via_sink(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        cell = Text("8.2k", style="green")
        ui.table(["Listeners"], [[cell, 42]])
        tbl = captured[0]
        first_col_cells = list(tbl.columns[0].cells)
        # The very same Text object lands in the table: styling intact.
        assert first_col_cells[0] is cell
        # Non-Text cells are still coerced with str().
        assert list(tbl.columns[1].cells) == ["42"]

    def test_preview_table_text_cell_preserved(self, reset_sinks):
        captured = []
        ui.set_preview_sink(captured.append)
        cell = Text("0.85", style="green")
        ui.preview_table(["Sim"], [[cell]])
        tbl = captured[0]
        assert list(tbl.columns[0].cells)[0] is cell


class TestOutputSink:
    def test_emit_routes_to_output_sink(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.info("routed message")
        ui.table(["H"], [["r"]])
        # Two emit() calls -> two renderables collected by the sink.
        assert len(captured) == 2

    def test_sink_text_carries_message(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.warning("hello sink")
        # rich Text renderable exposes .plain
        assert any(getattr(r, "plain", "") == "hello sink" for r in captured)


class TestPreviewSink:
    def test_preview_table_noop_without_sink(self, capsys, reset_sinks):
        # No preview sink set -> nothing rendered, no error.
        ui.preview_table(["H"], [["r"]], title="t")
        assert capsys.readouterr().out == ""

    def test_preview_table_routes_to_preview_sink(self, reset_sinks):
        captured = []
        ui.set_preview_sink(captured.append)
        ui.preview_table(["H"], [["r"]], title="Preview")
        assert len(captured) == 1
        assert captured[0] is not None

    def test_preview_table_without_title(self, reset_sinks):
        captured = []
        ui.set_preview_sink(captured.append)
        ui.preview_table(["H"], [["r"]])
        assert len(captured) == 1

    def test_clear_preview_emits_none(self, reset_sinks):
        captured = []
        ui.set_preview_sink(captured.append)
        ui.clear_preview()
        assert captured == [None]


class TestSparkline:
    TICKS = "▁▂▃▄▅▆▇█"

    def test_empty_series_is_empty_string(self):
        assert ui.sparkline([]) == ""

    def test_length_matches_input(self):
        assert len(ui.sparkline([1, 2, 3, 4, 5])) == 5

    def test_uses_only_block_ticks(self):
        spark = ui.sparkline([0, 1, 2, 3, 4, 5, 6, 7])
        assert all(ch in self.TICKS for ch in spark)

    def test_monotonic_increase_is_non_decreasing(self):
        idxs = [self.TICKS.index(ch) for ch in ui.sparkline([1, 2, 3, 4, 5])]
        assert idxs == sorted(idxs)
        assert idxs[0] == 0
        assert idxs[-1] == len(self.TICKS) - 1

    def test_flat_series_uses_constant_mid_tick(self):
        spark = ui.sparkline([3, 3, 3])
        assert len(spark) == 3
        # All identical: signals "no variation", not "no data".
        assert len(set(spark)) == 1


class TestBarChart:
    def test_renders_labels_and_values(self, capsys, reset_sinks):
        ui.bar_chart(["alpha", "beta"], [3, 9])
        out = capsys.readouterr().out
        assert "alpha" in out
        assert "beta" in out
        assert "9" in out
        assert "█" in out  # the peak value draws a full bar

    def test_empty_data_emits_message_not_error(self, capsys, reset_sinks):
        ui.bar_chart([], [])
        assert "No data" in capsys.readouterr().out

    def test_emits_single_renderable_to_sink(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.bar_chart(["a", "b"], [1, 2])
        assert len(captured) == 1

    def test_value_fmt_applied(self, capsys, reset_sinks):
        ui.bar_chart(["a"], [0.5], value_fmt=lambda v: f"{v * 100:.0f}%")
        assert "50%" in capsys.readouterr().out
