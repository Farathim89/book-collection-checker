"""ABS series names with a comma, and Audible series lists that hold a sibling series' volumes."""
from app.core.collect import split_series
from app.core.lookup import _names_other_series, _title_names

SPIN = "Trapped in a Dating Sim: Otome Games Are Tough for Us, Too!"
MAIN = "The World of Otome Games is Tough for Mobs"


def test_a_comma_inside_a_series_name():
    assert split_series(f"{SPIN} #1") == [f"{SPIN} #1"]
    assert split_series("Solo Leveling #2, Yen Audio #5") == ["Solo Leveling #2", "Yen Audio #5"]
    assert split_series("Overlord") == ["Overlord"]


def _p(title, *series):
    return {"title": title, "series": [{"asin": a, "title": t} for a, t in series]}


def test_a_main_series_volume_under_the_spin_off():
    vol7 = _p("Trapped in a Dating Sim: The World of Otome Games Is Tough for Mobs, Vol. 7", ("S", SPIN), ("M", MAIN))
    vol9 = _p("Trapped in a Dating Sim: The World of Otome Games Is Tough for Mobs, Vol. 9", ("S", SPIN))
    own = _p("Trapped in a Dating Sim: Otome Games Are Tough For Us, Too!, Vol. 2", ("S", SPIN))
    assert _names_other_series(vol7, "S", SPIN) and _names_other_series(vol9, "S", SPIN)
    assert not _names_other_series(own, "S", SPIN)
    assert _title_names(vol9, MAIN) and not _title_names(own, MAIN)


def test_a_typo_or_a_short_title_is_still_this_series():
    typo = _p("Mushoku Tensei: Jobless Reincarnatio, Vol. 18")
    assert not _names_other_series(typo, "X", "Mushoku Tensei: Jobless Reincarnation")
    year3 = _p("Classroom of the Elite: Year 3, Vol. 1")
    assert _names_other_series(year3, "X", "Classroom of the Elite: Year 2")
    assert not _names_other_series(_p("I Had That Same Dream Again"), "X", "The Yoru Sumino Collection")
