"""Unit tests for src/ui.py rendering helpers.

These render via rich to the module console; we assert they run without error
and (where practical) that key substrings reach stdout. A few tests route
through the output/preview sinks to capture renderables directly.
"""

from __future__ import annotations

import pytest
from rich import box
from rich.cells import cell_len
from rich.columns import Columns
from rich.panel import Panel
from rich.style import Style
from rich.text import Text

import ui


def style_at(text: Text, index: int):
    """Return the span style covering a character index (None when unstyled)."""
    for span in text.spans:
        if span.start <= index < span.end:
            return span.style
    return None


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

    def test_bars_are_tracked_hbars_with_bold_values(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.bar_chart(["a", "b"], [1, 2], width=4)
        bars = list(captured[0].columns[1].cells)
        # Peak row stays full blocks (pinned by test_profile's `"█" in out`).
        assert bars[1].plain == "████"
        # Non-peak rows draw the dim track remainder — presence reads on scale.
        assert bars[0].plain == "██╌╌"
        values = list(captured[0].columns[2].cells)
        assert isinstance(values[0], Text)
        assert values[0].style == ui.VALUE_STYLE


class TestHbar:
    def test_zero_fraction_is_all_track(self):
        assert ui.hbar(0.0, 8).plain == "╌" * 8

    def test_full_fraction_is_all_blocks(self):
        assert ui.hbar(1.0, 8).plain == "█" * 8

    def test_eighth_boundary_partial_glyph(self):
        assert ui.hbar(0.125, 1).plain == "▏"
        assert ui.hbar(1 / 16, 2).plain == "▏╌"

    def test_clamps_out_of_range_fractions(self):
        assert ui.hbar(1.5, 8).plain == "█" * 8
        assert ui.hbar(-0.5, 8).plain == "╌" * 8

    def test_nan_reads_as_zero(self):
        assert ui.hbar(float("nan"), 8).plain == "╌" * 8

    def test_tick_overlays_neutral_marker(self):
        bar = ui.hbar(0.0, 11, tick=0.5)
        idx = int(round(0.5 * 10))
        assert bar.plain[idx] == "┊"
        assert style_at(bar, idx) == ui.NEUTRAL_TICK_STYLE

    def test_tick_drawn_over_fill_too(self):
        bar = ui.hbar(1.0, 11, tick=0.5)
        assert bar.plain[5] == "┊"

    def test_zero_width_is_empty_text(self):
        assert ui.hbar(0.5, 0).plain == ""

    @pytest.mark.parametrize("fraction", [0.0, 0.1, 1 / 3, 0.5, 0.9, 1.0])
    @pytest.mark.parametrize("width", [1, 5, 24])
    def test_total_cell_len_always_equals_width(self, fraction, width):
        assert cell_len(ui.hbar(fraction, width).plain) == width

    def test_fill_and_track_styles(self):
        bar = ui.hbar(0.5, 8)
        assert style_at(bar, 0) == ui.BAR_STYLE
        assert style_at(bar, 7) == ui.TRACK_STYLE


class TestLollipop:
    def test_marker_positions(self):
        assert ui.lollipop(0.0, 9).plain.index("●") == 0
        assert ui.lollipop(0.5, 9).plain.index("●") == 4
        assert ui.lollipop(1.0, 9).plain.index("●") == 8

    def test_marker_wins_tick_collision(self):
        pip = ui.lollipop(0.5, 9, tick=0.5)
        assert pip.plain[4] == "●"
        assert "┊" not in pip.plain

    def test_tick_renders_when_not_colliding(self):
        pip = ui.lollipop(0.0, 9, tick=0.5)
        assert pip.plain[0] == "●"
        assert pip.plain[4] == "┊"

    def test_total_length(self):
        assert cell_len(ui.lollipop(0.7, 28).plain) == 28

    def test_marker_style(self):
        pip = ui.lollipop(0.5, 9)
        assert style_at(pip, 4) == ui.MARKER_STYLE

    def test_tiny_width_collapses_to_marker(self):
        pip = ui.lollipop(0.5, 1)
        assert pip.plain == "●"

    def test_clamps_fraction(self):
        assert ui.lollipop(7.0, 9).plain.index("●") == 8
        assert ui.lollipop(-1.0, 9).plain.index("●") == 0


class TestStackedBar:
    def test_exact_width_with_rounding_absorption(self):
        bar = ui.stacked_bar([(1 / 3, "red"), (1 / 3, "blue"), (1 / 3, "green")], width=10)
        assert cell_len(bar.plain) == 10
        assert bar.plain == " " * 10

    def test_sum_below_one_appends_missing_remainder(self):
        bar = ui.stacked_bar([(0.5, ui.FILL_STYLE)], width=10)
        assert cell_len(bar.plain) == 10
        assert bar.spans[-1].style == Style(bgcolor=ui.MISSING_STYLE)
        # The missing segment covers exactly the unfilled half.
        assert bar.spans[-1].end - bar.spans[-1].start == 5

    def test_sum_above_one_rescales_proportionally(self):
        bar = ui.stacked_bar([(1.0, "red"), (1.0, "blue")], width=10)
        assert cell_len(bar.plain) == 10
        assert len(bar.spans) == 2
        assert all(span.style != Style(bgcolor=ui.MISSING_STYLE) for span in bar.spans)
        assert bar.spans[0].end - bar.spans[0].start == 5

    def test_empty_parts_is_full_missing_bar(self):
        bar = ui.stacked_bar([], width=10)
        assert bar.plain == " " * 10
        assert len(bar.spans) == 1
        assert bar.spans[0].style == Style(bgcolor=ui.MISSING_STYLE)

    def test_negative_and_nan_fractions_read_as_zero(self):
        bar = ui.stacked_bar([(-0.5, "red"), (float("nan"), "blue")], width=10)
        assert bar.plain == " " * 10
        assert bar.spans[-1].style == Style(bgcolor=ui.MISSING_STYLE)

    def test_zero_width_is_empty_text(self):
        assert ui.stacked_bar([(0.5, "red")], width=0).plain == ""

    def test_style_objects_coerce_to_background(self):
        # A bg Style passes through; a fg-only Style paints its color as bg.
        bg = Style(bgcolor="red")
        fg = Style(color="blue")
        bar = ui.stacked_bar([(0.5, bg), (0.5, fg)], width=10)
        assert bar.spans[0].style is bg
        assert bar.spans[1].style == Style(bgcolor="blue")
        # A style with neither color survives untouched (defensive path).
        empty = Style(bold=True)
        assert ui.stacked_bar([(1.0, empty)], width=4).spans[0].style is empty


class TestSparklineText:
    def test_empty_is_empty_text(self):
        assert ui.sparkline_text([]).plain == ""

    def test_plain_matches_str_sparkline(self):
        values = [1, 5, 2, 8, 3]
        assert ui.sparkline_text(values).plain == ui.sparkline(values)

    def test_styles_ramp_from_low_to_high(self):
        spark = ui.sparkline_text([0, 1])
        assert style_at(spark, 0).color.triplet == ui.RAMP_LO
        assert style_at(spark, 1).color.triplet == ui.RAMP_HI

    def test_flat_series_mid_ticks_at_mid_color(self):
        spark = ui.sparkline_text([3, 3, 3])
        assert spark.plain == ui.sparkline([3, 3, 3])
        styles = {str(style_at(spark, i)) for i in range(3)}
        assert len(styles) == 1

    def test_single_value_renders_one_mid_tick(self):
        spark = ui.sparkline_text([7])
        assert len(spark.plain) == 1
        assert spark.plain == ui.sparkline([7])


class TestHeatStrip:
    def test_absolute_all_zero_is_uniformly_cold(self):
        strip = ui.heat_strip([0.0, 0.0, 0.0])
        assert len(strip.spans) == 3
        assert len({str(span.style) for span in strip.spans}) == 1
        assert strip.spans[0].style.bgcolor.triplet == ui.RAMP_LO

    def test_cold_and_hot_endpoints(self):
        strip = ui.heat_strip([0.0, 1.0])
        assert strip.spans[0].style.bgcolor.triplet == ui.RAMP_LO
        assert strip.spans[1].style.bgcolor.triplet == ui.RAMP_HI

    def test_never_min_max_rescaled(self):
        # 0.4 is the series max but must NOT render at the hot end.
        strip = ui.heat_strip([0.2, 0.4])
        assert strip.spans[1].style.bgcolor.triplet != ui.RAMP_HI

    def test_empty_is_empty_text(self):
        assert ui.heat_strip([]).plain == ""

    def test_single_value_one_cell(self):
        strip = ui.heat_strip([0.5], cell_width=2)
        assert strip.plain == "  "

    def test_values_clamped(self):
        strip = ui.heat_strip([-1.0, 2.0])
        assert strip.spans[0].style.bgcolor.triplet == ui.RAMP_LO
        assert strip.spans[1].style.bgcolor.triplet == ui.RAMP_HI

    def test_cell_width_respected(self):
        assert cell_len(ui.heat_strip([0.1, 0.9], cell_width=3).plain) == 6

    def test_sub_one_cell_width_is_empty_text(self):
        assert ui.heat_strip([0.5], cell_width=0).plain == ""


class TestChips:
    def test_values_joined_with_dim_separators(self):
        text = ui.chips(["dream pop", "shoegaze"])
        assert text.plain == "dream pop · shoegaze"
        assert style_at(text, 0) == ui.ACCENT_BLUE

    def test_truncation_appends_dim_count(self):
        text = ui.chips(["a", "b", "c", "d", "e"], max_items=3)
        assert text.plain == "a · b · c +2"

    def test_empty_is_honest_dim_dash(self):
        text = ui.chips([])
        assert text.plain == "—"
        assert text.style == "dim"

    def test_single_value(self):
        assert ui.chips(["lo-fi"]).plain == "lo-fi"


class TestStatCards:
    def test_emits_one_columns_of_rounded_panels(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.stat_cards(
            [
                ("Built from", "180 tracks", "your rotation"),
                ("Sonic data", "56/180", None),
            ]
        )
        assert len(captured) == 1
        cols = captured[0]
        assert isinstance(cols, Columns)
        panels = list(cols.renderables)
        assert len(panels) == 2
        for panel in panels:
            assert isinstance(panel, Panel)
            assert panel.box is box.ROUNDED
            assert panel.border_style == ui.CARD_BORDER
            assert panel.width == 22

    def test_long_title_truncated_with_ellipsis(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.stat_cards([("A very long stat card title", "1", None)], width=22)
        title = list(captured[0].renderables)[0].title
        assert title.plain.endswith("…")
        assert cell_len(title.plain) <= 22 - 6

    def test_distinct_artists_title_not_truncated_at_default_width(self, reset_sinks):
        # "Distinct artists" is 16 chars == width 22 - 6: pinned literal must survive.
        captured = []
        ui.set_output_sink(captured.append)
        ui.stat_cards([("Distinct artists", "180", "one per track")])
        assert list(captured[0].renderables)[0].title.plain == "Distinct artists"

    def test_empty_cards_emit_nothing(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.stat_cards([])
        assert captured == []

    def test_value_and_sub_center_independently(self, reset_sinks):
        # One Align around the whole Group centers the block but left-aligns
        # the shorter line inside it ("Career Boy" hugging the left edge under
        # "Dorian Electra · p=0.96") — every line must center on its own.
        from rich.align import Align

        captured = []
        ui.set_output_sink(captured.append)
        ui.stat_cards([("HAPPIEST", "Career Boy", "Dorian Electra · p=0.96")], width=30)
        panel = list(captured[0].renderables)[0]
        lines = list(panel.renderable.renderables)
        assert len(lines) == 2
        assert all(isinstance(line, Align) and line.align == "center" for line in lines)


class TestChartPanel:
    def _grid(self, panel):
        return panel.renderable

    def test_rows_render_in_given_order(self, reset_sinks):
        panel = ui.chart_panel("T", [("b", 1), ("a", 2)], emit=False)
        labels = list(self._grid(panel).columns[0].cells)
        assert labels == ["b", "a"]

    def test_peak_scaled_bars(self, reset_sinks):
        panel = ui.chart_panel("T", [("a", 2), ("b", 4)], width=8, emit=False)
        bars = [cell.plain for cell in self._grid(panel).columns[1].cells]
        assert bars[1] == "█" * 8  # the peak fills the bar
        assert bars[0] == "████" + "╌" * 4

    def test_max_value_absolute_scale(self, reset_sinks):
        panel = ui.chart_panel("T", [("p", 0.5)], width=8, max_value=1.0, emit=False)
        bar = list(self._grid(panel).columns[1].cells)[0].plain
        assert bar == "████" + "╌" * 4

    def test_empty_rows_render_dim_no_data(self, reset_sinks):
        panel = ui.chart_panel("T", [], emit=False)
        assert isinstance(panel.renderable, Text)
        assert panel.renderable.plain == "no data"
        assert panel.renderable.style == "dim"

    def test_all_zero_values_keep_presence_visible(self, reset_sinks):
        panel = ui.chart_panel("T", [("a", 0), ("b", 0)], width=6, emit=False)
        bars = [cell.plain for cell in self._grid(panel).columns[1].cells]
        assert bars == ["╌" * 6, "╌" * 6]
        assert list(self._grid(panel).columns[2].cells) == ["0", "0"]

    def test_lollipop_kind_draws_markers(self, reset_sinks):
        panel = ui.chart_panel(
            "T", [("d", 0.66)], kind="lollipop", width=9, max_value=1.0, tick=0.5, emit=False
        )
        bar = list(self._grid(panel).columns[1].cells)[0].plain
        assert "●" in bar
        assert "█" not in bar
        assert "┊" in bar

    def test_caption_is_dim_italic_subtitle(self, reset_sinks):
        panel = ui.chart_panel("T", [("a", 1)], caption="tracks tagged", emit=False)
        assert panel.subtitle.plain == "tracks tagged"
        assert panel.subtitle.style == ui.CAPTION_STYLE
        assert panel.subtitle_align == "right"

    def test_panel_chrome(self, reset_sinks):
        panel = ui.chart_panel("Era fingerprint", [("2020s", 133)], emit=False)
        assert panel.box is box.ROUNDED
        assert panel.border_style == ui.CARD_BORDER
        assert panel.title.plain == "Era fingerprint"
        assert panel.title.style == ui.SUBSECTION_STYLE
        assert panel.title_align == "left"

    def test_value_fmt_applied(self, reset_sinks):
        panel = ui.chart_panel("T", [("a", 0.5)], value_fmt=lambda v: f"{v:.2f}", emit=False)
        assert list(self._grid(panel).columns[2].cells) == ["0.50"]

    def test_emit_true_routes_to_sink(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        panel = ui.chart_panel("T", [("a", 1)])
        assert captured == [panel]

    def test_emit_false_does_not_emit(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.chart_panel("T", [("a", 1)], emit=False)
        assert captured == []


class TestFacetColumns:
    def test_empty_blocks_noop(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.facet_columns([])
        assert captured == []

    def test_single_block_emitted_alone(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.facet_columns([("Moods", [("dreamy", 20)], "tracks tagged")])
        assert len(captured) == 1
        assert isinstance(captured[0], Panel)
        assert captured[0].width == 44

    def test_two_blocks_in_one_columns(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.facet_columns(
            [
                ("Moods", [("dreamy", 20)], None),
                ("Genres", [("indie rock", 82)], "tracks tagged"),
            ],
            panel_width=40,
        )
        assert len(captured) == 1
        assert isinstance(captured[0], Columns)
        panels = list(captured[0].renderables)
        assert [p.title.plain for p in panels] == ["Moods", "Genres"]
        assert all(p.width == 40 for p in panels)

    def test_block_with_empty_rows_shows_no_data(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.facet_columns([("Moods", [], None)])
        assert captured[0].renderable.plain == "no data"


class TestCoveragePanel:
    def test_rows_render_counts_and_percent(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.coverage_panel("Data Coverage", [("context", 50, 100), ("sonic", 487, 1284)])
        panel = captured[0]
        assert isinstance(panel, Panel)
        grid = panel.renderable
        assert list(grid.columns[2].cells) == ["50/100", "487/1284"]
        assert list(grid.columns[3].cells) == ["(50%)", "(38%)"]

    def test_zero_total_row_renders_honest_blank(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.coverage_panel("C", [("ok", 1, 2), ("empty", 0, 0)])
        grid = captured[0].renderable
        assert list(grid.columns[2].cells) == ["1/2", "0/0"]
        assert list(grid.columns[3].cells) == ["(50%)", "—"]
        # The 0/0 bar is a full missing-grey segment.
        bar = list(grid.columns[1].cells)[1]
        assert bar.spans[0].style == Style(bgcolor=ui.MISSING_STYLE)

    def test_all_zero_totals_become_notice(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.coverage_panel("C", [("a", 0, 0), ("b", 0, 0)])
        assert len(captured) == 1
        assert isinstance(captured[0], Text)
        assert captured[0].plain == "No data to chart."
        assert captured[0].style == "dim"

    def test_no_rows_becomes_notice(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.coverage_panel("C", [])
        assert captured[0].plain == "No data to chart."

    def test_have_above_total_clamps(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.coverage_panel("C", [("over", 5, 3)])
        grid = captured[0].renderable
        assert list(grid.columns[2].cells) == ["3/3"]
        assert list(grid.columns[3].cells) == ["(100%)"]

    def test_caption_is_subtitle(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.coverage_panel("C", [("a", 1, 2)], caption="grey = not there yet")
        assert captured[0].subtitle.plain == "grey = not there yet"
        assert captured[0].subtitle.style == ui.CAPTION_STYLE

    def test_none_title_renders_untitled_panel(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.coverage_panel(None, [("a", 1, 2)], caption="grey = not there yet")
        panel = captured[0]
        assert isinstance(panel, Panel)
        assert panel.title is None
        assert panel.subtitle.plain == "grey = not there yet"

    def test_long_caption_demoted_below_panel(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        long_caption = "grey = not there yet · sonic comes from AcousticBrainz lookups"
        ui.coverage_panel(None, [("a", 1, 2)], width=8, caption=long_caption)
        panel = captured[0]
        assert panel.width is None  # the caption never widens the panel
        assert panel.subtitle is None
        assert captured[1].plain == long_caption
        assert captured[1].style == ui.CAPTION_STYLE

    def test_bar_clamped_so_labels_and_counts_survive(self, reset_sinks, monkeypatch):
        # At a 60-cell console a 36-cell bar would crush every column into
        # ellipsis soup; only the (decorative) bar may shrink.
        from rich.console import Console

        monkeypatch.setattr(ui, "console", Console(width=60))
        captured = []
        ui.set_output_sink(captured.append)
        ui.coverage_panel(None, [("embeddings", 1257, 1284)], width=36)
        grid = captured[0].renderable
        bar = list(grid.columns[1].cells)[0]
        overhead = cell_len("embeddings") + cell_len("1257/1284") + 6 + 12
        assert cell_len(bar.plain) == 60 - overhead
        assert list(grid.columns[2].cells) == ["1257/1284"]  # counts intact

    def test_bar_keeps_requested_width_on_wide_console(self, reset_sinks, monkeypatch):
        from rich.console import Console

        monkeypatch.setattr(ui, "console", Console(width=100))
        captured = []
        ui.set_output_sink(captured.append)
        ui.coverage_panel(None, [("embeddings", 1257, 1284)], width=36)
        bar = list(captured[0].renderable.columns[1].cells)[0]
        assert cell_len(bar.plain) == 36

    def test_bar_never_collapses_below_eight_cells(self, reset_sinks, monkeypatch):
        from rich.console import Console

        monkeypatch.setattr(ui, "console", Console(width=20))
        captured = []
        ui.set_output_sink(captured.append)
        ui.coverage_panel(None, [("embeddings", 1257, 1284)], width=36)
        bar = list(captured[0].renderable.columns[1].cells)[0]
        assert cell_len(bar.plain) == 8


class TestSideBySide:
    def test_no_renderables_noop(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.side_by_side()
        assert captured == []

    def test_single_renderable_emitted_directly(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        only = Text("solo")
        ui.side_by_side(only)
        assert captured == [only]

    def test_multiple_renderables_in_columns(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        a, b = Text("a"), Text("b")
        ui.side_by_side(a, b)
        assert isinstance(captured[0], Columns)
        assert list(captured[0].renderables) == [a, b]


class TestInsightAndCaption:
    def test_insight_prefixes_cyan_diamond(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.insight("Tempo spans 82–185 BPM.")
        text = captured[0]
        assert text.plain == "◆ Tempo spans 82–185 BPM."
        assert style_at(text, 0) == ui.ACCENT_BLUE

    def test_caption_is_dim_italic(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.caption("a ranking, not a match %")
        assert captured[0].plain == "a ranking, not a match %"
        assert captured[0].style == ui.CAPTION_STYLE


class TestTextLine:
    def test_composes_parts_into_one_line(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.text_line(
            Text("rotation runway ", style="dim"),
            ui.stacked_bar([(0.5, "cyan")], 4),
            Text(" 2 played", style="dim"),
        )
        assert len(captured) == 1
        line = captured[0]
        assert isinstance(line, Text)
        assert line.plain == "rotation runway      2 played"

    def test_parts_keep_their_styles(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.text_line(Text("Generation 20", style="bold cyan"), Text(" · 10 tracks", style="dim"))
        line = captured[0]
        assert style_at(line, 0) == "bold cyan"
        assert style_at(line, len("Generation 20")) == "dim"

    def test_plain_strings_pass_unstyled(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.text_line("  ", Text("x", style="dim"))
        assert captured[0].plain == "  x"

    def test_no_parts_is_noop(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.text_line()
        assert captured == []

    def test_silenced_in_json_mode(self, capsys, reset_sinks):
        ui.set_json_mode(True)
        try:
            ui.text_line(Text("hidden"))
        finally:
            ui.set_json_mode(False)
        assert capsys.readouterr().out == ""


class TestInkJsonModeSilencing:
    """Every new emitter must produce nothing in --json mode (the `_emit`
    choke point silences them); builders never emit at all."""

    def test_emitters_silenced(self, capsys, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.set_json_mode(True)
        try:
            ui.stat_cards([("T", "1", None)])
            ui.chart_panel("T", [("a", 1)])
            ui.facet_columns([("T", [("a", 1)], None)])
            ui.coverage_panel("T", [("a", 1, 2)])
            ui.coverage_panel("T", [])  # the notice path is silenced too
            ui.side_by_side(Text("x"))
            ui.insight("finding")
            ui.caption("footnote")
        finally:
            ui.set_json_mode(False)
        result = capsys.readouterr()
        assert result.out == ""
        assert result.err == ""
        assert captured == []

    def test_builders_never_emit(self, capsys, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.hbar(0.5)
        ui.lollipop(0.5)
        ui.stacked_bar([(0.5, "cyan")])
        ui.sparkline_text([1, 2])
        ui.heat_strip([0.5])
        ui.chips(["a"])
        assert captured == []
        assert capsys.readouterr().out == ""


class TestChartPanelFooterRows:
    def _grid(self, panel):
        return panel.renderable

    def test_footer_rows_appended_after_spacer(self, reset_sinks):
        axis = Text("60 ─●─ 190 BPM")
        panel = ui.chart_panel("T", [("a", 1)], footer_rows=[("tempo", axis, "")], emit=False)
        labels = list(self._grid(panel).columns[0].cells)
        assert labels == ["a", "", "tempo"]  # data row, spacer, footer label
        middles = list(self._grid(panel).columns[1].cells)
        assert middles[-1] is axis  # footer middle passes through unscaled

    def test_footer_rows_ignored_when_no_data(self, reset_sinks):
        panel = ui.chart_panel("T", [], footer_rows=[("tempo", Text("x"), "")], emit=False)
        assert isinstance(panel.renderable, Text)
        assert panel.renderable.plain == "no data"

    def test_no_footer_means_no_spacer_row(self, reset_sinks):
        panel = ui.chart_panel("T", [("a", 1)], emit=False)
        assert list(self._grid(panel).columns[0].cells) == ["a"]


class TestInkPanel:
    def test_chrome_matches_chart_panel(self, reset_sinks):
        panel = ui.ink_panel(Text("body"), title="The core", caption="top quartile", emit=False)
        assert panel.box is box.ROUNDED
        assert panel.border_style == ui.CARD_BORDER
        assert panel.title.plain == "The core"
        assert panel.title.style == ui.SUBSECTION_STYLE
        assert panel.title_align == "left"
        assert panel.subtitle.plain == "top quartile"
        assert panel.subtitle.style == ui.CAPTION_STYLE
        assert panel.subtitle_align == "right"

    def test_title_and_caption_optional(self, reset_sinks):
        panel = ui.ink_panel(Text("masthead"), emit=False)
        assert panel.title is None
        assert panel.subtitle is None

    def test_border_style_and_width_overrides(self, reset_sinks):
        panel = ui.ink_panel(Text("x"), border_style=ui.MARKER_STYLE, width=44, emit=False)
        assert panel.border_style == ui.MARKER_STYLE
        assert panel.width == 44

    def test_emit_true_routes_to_sink(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        panel = ui.ink_panel(Text("x"))
        assert captured == [panel]

    def test_emit_false_does_not_emit(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        ui.ink_panel(Text("x"), emit=False)
        assert captured == []

    def test_silenced_in_json_mode(self, capsys, reset_sinks):
        ui.set_json_mode(True)
        try:
            ui.ink_panel(Text("x"))
        finally:
            ui.set_json_mode(False)
        assert capsys.readouterr().out == ""


class TestChromeFitWidth:
    """The subtitle is the honesty-caption slot — it must never be silently
    cropped (Rich hard-crops border chrome with NO ellipsis) and it must never
    dictate panel layout. A caption that outgrows the panel or the console is
    demoted to a wrapped `caption()` line below the panel when emitting; only
    the title may widen a panel (clamped to the console)."""

    def test_long_caption_demoted_to_line_below_panel(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        long_caption = "175 of 180 datable from enriched era tags · 1 defied parsing"
        panel = ui.chart_panel("Era", [("2020s", 1)], width=8, caption=long_caption)
        assert captured[0] is panel
        assert panel.width is None  # the caption never widens the panel
        assert panel.subtitle is None  # ...nor stays to be border-cropped
        assert captured[1].plain == long_caption  # full text, wrapped, below
        assert captured[1].style == ui.CAPTION_STYLE

    def test_fitting_caption_stays_on_border(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        panel = ui.chart_panel("T", [("a-very-long-label-goes-here", 1)], caption="ok")
        assert panel.subtitle is not None
        assert panel.subtitle.plain == "ok"
        assert captured == [panel]  # no demoted caption line

    def test_long_title_floors_panel_width(self, reset_sinks):
        long_title = "Acoustic profile · 56/180 seed tracks (AcousticBrainz)"
        panel = ui.chart_panel(long_title, [("a", 1)], width=8, emit=False)
        assert panel.width == cell_len(long_title) + 6

    def test_title_clamped_to_console_with_visible_ellipsis(self, reset_sinks):
        avail = ui.console.options.max_width
        panel = ui.chart_panel("t" * (avail + 20), [("a", 1)], width=8, emit=False)
        assert panel.width == avail  # never wider than the render surface
        assert panel.title.plain.endswith("…")  # the cut is visible, not silent
        assert cell_len(panel.title.plain) <= avail - 6

    def test_wide_body_keeps_width_unset(self, reset_sinks):
        panel = ui.chart_panel(
            "T", [("a-very-long-label-here", 1)], width=36, caption="ok", emit=False
        )
        assert panel.width is None  # body already wider than the chrome

    def test_explicit_width_truncates_subtitle_with_ellipsis(self, reset_sinks):
        panel = ui.ink_panel(
            Text("x"), caption="a very long caption indeed " * 3, width=44, emit=False
        )
        assert panel.width == 44  # caller-fixed widths are their responsibility
        # ...but the subtitle is pre-cut with a visible `…`, never border-cropped.
        assert panel.subtitle.plain.endswith("…")
        assert cell_len(panel.subtitle.plain) <= 44 - 6

    def test_ink_panel_demotes_caption_when_emitting(self, reset_sinks):
        captured = []
        ui.set_output_sink(captured.append)
        caption = "named from enriched tags on 178/180 tracks · semantic profile, not acoustic"
        panel = ui.ink_panel(Text("short"), caption=caption)
        assert panel.width is None  # content-sized: no dead interior
        assert panel.subtitle is None
        assert captured[0] is panel
        assert captured[1].plain == caption
        assert captured[1].style == ui.CAPTION_STYLE

    def test_composed_panel_keeps_caption_attached(self, reset_sinks):
        # emit=False composition: nothing can be emitted below, so the panel
        # widens (clamped to the console) and keeps the caption on the border.
        caption = "named from enriched tags on 178/180 tracks · semantic profile, not acoustic"
        panel = ui.ink_panel(Text("short"), caption=caption, emit=False)
        avail = ui.console.options.max_width
        assert panel.width == min(cell_len(caption) + 6, avail)
        assert panel.subtitle is not None
