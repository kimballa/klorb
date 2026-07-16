# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.widgets.palette."""

from textual.command import DiscoveryHit, Hit

from klorb.tui.widgets.palette import _sort_key


def _hit(score: float, text: str) -> Hit:
    return Hit(score, text, lambda: None, text=text)


def test_sort_key_ranks_higher_score_first() -> None:
    hits = [_hit(0.4, "Zebra"), _hit(0.9, "Apple")]

    hits.sort(key=_sort_key)

    assert [str(hit.text) for hit in hits] == ["Apple", "Zebra"]


def test_sort_key_breaks_equal_scores_alphabetically_case_insensitive() -> None:
    hits = [_hit(0.5, "banana"), _hit(0.5, "Apple"), _hit(0.5, "cherry")]

    hits.sort(key=_sort_key)

    assert [str(hit.text) for hit in hits] == ["Apple", "banana", "cherry"]


def test_sort_key_puts_score_ahead_of_alphabetical_order() -> None:
    """A lower-scored hit that would sort first alphabetically must still land after a
    higher-scored hit that sorts later alphabetically — score is the primary key, alphabetical
    order is only a tiebreak.
    """
    hits = [_hit(0.9, "Zebra"), _hit(0.2, "Apple")]

    hits.sort(key=_sort_key)

    assert [str(hit.text) for hit in hits] == ["Zebra", "Apple"]


def test_sort_key_treats_discovery_hits_as_all_tied_at_zero() -> None:
    """`DiscoveryHit.score` is hardcoded to `0.0`, so a list of them ties entirely on score
    and the alphabetical tiebreak alone determines the order (the bare `>` full listing).
    """
    hits: list[DiscoveryHit] = [
        DiscoveryHit("Zebra", lambda: None), DiscoveryHit("Apple", lambda: None)]

    hits.sort(key=_sort_key)

    assert [str(hit.text) for hit in hits] == ["Apple", "Zebra"]
