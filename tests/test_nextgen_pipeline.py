from nextgen.pipeline import _extract_year_target


def test_extract_year_target_two_digit_decades_not_future():
    # Two-digit decades resolve to the midpoint of the decade. The 20xx
    # reading is preferred only when it is not in the future; otherwise the
    # 19xx reading is used. These hold for any current year >= 2025.
    assert _extract_year_target("80s music") == 1985
    assert _extract_year_target("90s britpop") == 1995
    assert _extract_year_target("roaring 20s") == 2025
    assert _extract_year_target("30s swing") == 1935
    assert _extract_year_target("40s jazz") == 1945


def test_extract_year_target_four_digit_decade():
    assert _extract_year_target("indie 2010s") == 2015
    assert _extract_year_target("1990s shoegaze") == 1995


def test_extract_year_target_bare_year():
    assert _extract_year_target("released in 1999") == 1999
    assert _extract_year_target("late night jazz from 1998") == 1998


def test_extract_year_target_none_when_absent():
    assert _extract_year_target("uplifting acoustic folk") is None
