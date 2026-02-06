"""
Unit tests for OpenAI web search/score wrapper JSON parsing.
Validates regex patterns correctly extract fenced code blocks.
"""
import pytest

from openai_web_search_wrapper import _parse_json_output as search_parse
from openai_web_score_wrapper import _parse_json_output as score_parse


class TestSearchWrapperJsonParsing:
    """Tests for openai_web_search_wrapper._parse_json_output."""

    def test_plain_json(self):
        text = '{"summary": "test", "results": []}'
        result = search_parse(text)
        assert result == {"summary": "test", "results": []}

    def test_fenced_json_block(self):
        text = '```json\n{"summary": "fenced", "results": []}\n```'
        result = search_parse(text)
        assert result == {"summary": "fenced", "results": []}

    def test_fenced_json_block_with_whitespace(self):
        text = '```json\n  {"summary": "ws", "results": []}  \n```'
        result = search_parse(text)
        assert result == {"summary": "ws", "results": []}

    def test_fenced_generic_block(self):
        text = '```\n{"summary": "generic", "results": []}\n```'
        result = search_parse(text)
        assert result == {"summary": "generic", "results": []}

    def test_json_embedded_in_text(self):
        text = 'Here are the results:\n{"summary": "embedded", "results": []}\nDone.'
        result = search_parse(text)
        assert result is not None
        assert result["summary"] == "embedded"

    def test_json_array(self):
        text = '[{"song": "A", "artist": "B"}]'
        result = search_parse(text)
        assert isinstance(result, list)
        assert result[0]["song"] == "A"

    def test_empty_string(self):
        assert search_parse("") is None

    def test_non_json_text(self):
        result = search_parse("No JSON here, just some plain text.")
        assert result is None

    def test_fenced_json_case_insensitive(self):
        text = '```JSON\n{"summary": "upper", "results": []}\n```'
        result = search_parse(text)
        assert result is not None
        assert result["summary"] == "upper"

    def test_json_with_surrounding_commentary(self):
        text = (
            "Based on the search results, here is the output:\n"
            "```json\n"
            '{"summary": "commentary", "results": [{"song": "X"}]}\n'
            "```\n"
            "Let me know if you need more."
        )
        result = search_parse(text)
        assert result is not None
        assert result["summary"] == "commentary"
        assert len(result["results"]) == 1


class TestScoreWrapperJsonParsing:
    """Tests for openai_web_score_wrapper._parse_json_output."""

    def test_plain_json(self):
        text = '{"scores": {"track1": 0.9}}'
        result = score_parse(text)
        assert result == {"scores": {"track1": 0.9}}

    def test_fenced_json_block(self):
        text = '```json\n{"scores": {"track1": 0.8}}\n```'
        result = score_parse(text)
        assert result == {"scores": {"track1": 0.8}}

    def test_fenced_generic_block(self):
        text = '```\n{"scores": {"track1": 0.7}}\n```'
        result = score_parse(text)
        assert result == {"scores": {"track1": 0.7}}

    def test_empty_string(self):
        assert score_parse("") is None

    def test_json_embedded_in_text(self):
        text = 'Here: {"scores": {"t1": 0.5}} done'
        result = score_parse(text)
        assert result is not None
        assert result["scores"]["t1"] == 0.5

    def test_non_json_text(self):
        assert score_parse("No JSON here.") is None

    def test_multiline_fenced_json(self):
        text = (
            "```json\n"
            "{\n"
            '  "scores": {\n'
            '    "track1": 0.95,\n'
            '    "track2": 0.42\n'
            "  }\n"
            "}\n"
            "```"
        )
        result = score_parse(text)
        assert result is not None
        assert result["scores"]["track1"] == 0.95
        assert result["scores"]["track2"] == 0.42
