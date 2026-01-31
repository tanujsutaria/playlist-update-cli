import pytest

from nextgen.pipeline import _extract_year_target
from nextgen.scoring import ScoreConfig, score_candidates


def test_score_candidates_metadata_boosts():
    query_vec = [1.0, 0.0]
    track_vecs = [[1.0, 0.0]]
    strict_ratios = [0.0]
    config = ScoreConfig(
        base_weight=0.6,
        strict_weight=0.0,
        source_weight=0.1,
        year_weight=0.2,
        year_tolerance=10,
        source_cap=5,
        year_target=2000,
    )
    metadata = [{"sources_count": 5, "year": "2000"}]
    scores = score_candidates(query_vec, track_vecs, strict_ratios, config, metadata=metadata)

    assert scores[0] == pytest.approx(0.9, rel=1e-6)


def test_extract_year_target():
    assert _extract_year_target("late night jazz from 1998") == 1998
    assert _extract_year_target("dreamy 1990s shoegaze") == 1995
    assert _extract_year_target("britpop 90s") == 1995
    assert _extract_year_target("indie 2010s") == 2015
