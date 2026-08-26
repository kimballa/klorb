# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.file_activity.FileActivityTracker."""

from klorb.tui.file_activity import FileActivityEntry, FileActivityTracker


def test_records_a_new_path_with_its_given_mode() -> None:
    tracker = FileActivityTracker()

    tracker.record("/a.txt", "read")

    assert tracker.entries() == [FileActivityEntry(abs_path="/a.txt", mode="read")]


def test_preserves_first_access_order() -> None:
    tracker = FileActivityTracker()

    tracker.record("/a.txt", "read")
    tracker.record("/b.txt", "write")
    tracker.record("/c.txt", "read")

    assert [entry.abs_path for entry in tracker.entries()] == ["/a.txt", "/b.txt", "/c.txt"]


def test_a_later_write_upgrades_a_read_entry() -> None:
    tracker = FileActivityTracker()

    tracker.record("/a.txt", "read")
    tracker.record("/a.txt", "write")

    assert tracker.entries() == [FileActivityEntry(abs_path="/a.txt", mode="write")]


def test_a_later_read_does_not_downgrade_a_write_entry() -> None:
    tracker = FileActivityTracker()

    tracker.record("/a.txt", "write")
    tracker.record("/a.txt", "read")

    assert tracker.entries() == [FileActivityEntry(abs_path="/a.txt", mode="write")]


def test_a_repeated_read_does_not_duplicate_the_entry() -> None:
    tracker = FileActivityTracker()

    tracker.record("/a.txt", "read")
    tracker.record("/a.txt", "read")

    assert tracker.entries() == [FileActivityEntry(abs_path="/a.txt", mode="read")]
