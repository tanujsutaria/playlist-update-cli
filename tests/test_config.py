"""Unit tests for src/config.py: env_* coercion helpers and AppConfig."""

from __future__ import annotations

from config import AppConfig, env_flag, env_float, env_int


class TestEnvFloat:
    def test_valid_value(self):
        assert env_float("X", 1.0, env={"X": "2.5"}) == 2.5

    def test_integer_string_value(self):
        assert env_float("X", 1.0, env={"X": "3"}) == 3.0

    def test_empty_string_falls_back(self):
        assert env_float("X", 1.0, env={"X": ""}) == 1.0

    def test_unset_falls_back(self):
        assert env_float("X", 1.0, env={}) == 1.0

    def test_invalid_falls_back(self):
        assert env_float("X", 1.0, env={"X": "not-a-number"}) == 1.0


class TestEnvInt:
    def test_valid_value(self):
        assert env_int("N", 5, env={"N": "12"}) == 12

    def test_empty_string_falls_back(self):
        assert env_int("N", 5, env={"N": ""}) == 5

    def test_unset_falls_back(self):
        assert env_int("N", 5, env={}) == 5

    def test_invalid_falls_back(self):
        assert env_int("N", 5, env={"N": "3.5"}) == 5

    def test_non_numeric_falls_back(self):
        assert env_int("N", 5, env={"N": "abc"}) == 5


class TestEnvFlag:
    def test_truthy_tokens(self):
        for token in ("1", "true", "yes", "on"):
            assert env_flag("F", env={"F": token}) is True

    def test_truthy_case_insensitive(self):
        for token in ("TRUE", "Yes", "On", "TrUe"):
            assert env_flag("F", env={"F": token}) is True

    def test_truthy_with_whitespace(self):
        assert env_flag("F", env={"F": "  true  "}) is True

    def test_falsey_tokens(self):
        for token in ("0", "false", "no", "off", "nope"):
            assert env_flag("F", env={"F": token}) is False

    def test_empty_string_uses_default(self):
        assert env_flag("F", default=True, env={"F": ""}) is True
        assert env_flag("F", default=False, env={"F": ""}) is False

    def test_unset_uses_default(self):
        assert env_flag("F", default=True, env={}) is True
        assert env_flag("F", default=False, env={}) is False


class TestAppConfigDefaults:
    def test_dataclass_defaults(self):
        cfg = AppConfig()
        assert cfg.model_name == "all-mpnet-base-v2"
        assert cfg.strict_threshold == 0.6
        assert cfg.lenient_threshold == 0.75
        assert cfg.strict_weight == 0.4
        assert cfg.base_weight == 0.6
        assert cfg.source_weight == 0.05
        assert cfg.year_weight == 0.05
        assert cfg.year_tolerance == 10
        assert cfg.source_cap == 5

    def test_is_frozen(self):
        import dataclasses

        cfg = AppConfig()
        try:
            cfg.model_name = "other"  # type: ignore[misc]
        except dataclasses.FrozenInstanceError:
            return
        raise AssertionError("AppConfig should be frozen")


class TestAppConfigFromEnv:
    def test_empty_env_matches_defaults(self):
        assert AppConfig.from_env(env={}) == AppConfig()

    def test_picks_up_overrides(self):
        cfg = AppConfig.from_env(
            env={
                "SEARCH_EMBEDDING_MODEL": "custom-model",
                "SEARCH_STRICT_THRESHOLD": "0.9",
                "SEARCH_LENIENT_THRESHOLD": "0.8",
                "SEARCH_SCORE_STRICT_WEIGHT": "0.3",
                "SEARCH_SCORE_BASE_WEIGHT": "0.7",
                "SEARCH_SCORE_SOURCE_WEIGHT": "0.1",
                "SEARCH_SCORE_YEAR_WEIGHT": "0.2",
                "SEARCH_SCORE_YEAR_TOLERANCE": "3",
                "SEARCH_SCORE_SOURCE_CAP": "8",
            }
        )
        assert cfg.model_name == "custom-model"
        assert cfg.strict_threshold == 0.9
        assert cfg.lenient_threshold == 0.8
        assert cfg.strict_weight == 0.3
        assert cfg.base_weight == 0.7
        assert cfg.source_weight == 0.1
        assert cfg.year_weight == 0.2
        assert cfg.year_tolerance == 3
        assert cfg.source_cap == 8

    def test_partial_override_keeps_other_defaults(self):
        cfg = AppConfig.from_env(env={"SEARCH_STRICT_THRESHOLD": "0.42"})
        assert cfg.strict_threshold == 0.42
        # untouched values fall back to defaults
        assert cfg.model_name == "all-mpnet-base-v2"
        assert cfg.year_tolerance == 10

    def test_invalid_numeric_falls_back_to_default(self):
        cfg = AppConfig.from_env(
            env={"SEARCH_STRICT_THRESHOLD": "garbage", "SEARCH_SCORE_YEAR_TOLERANCE": ""}
        )
        assert cfg.strict_threshold == 0.6
        assert cfg.year_tolerance == 10
