"""Unit tests for src/ui.py rendering helpers.

These render via rich to the module console; we assert they run without error
and (where practical) that key substrings reach stdout. A few tests route
through the output/preview sinks to capture renderables directly.
"""

from __future__ import annotations

import pytest

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
