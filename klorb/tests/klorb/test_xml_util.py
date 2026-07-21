# © Copyright 2026 Aaron Kimball
"""Tests for klorb.xml_util."""

from xml.etree import ElementTree

from klorb.xml_util import cdata


def test_cdata_wraps_text_verbatim() -> None:
    assert cdata("hello") == "<![CDATA[hello]]>"


def test_cdata_escapes_embedded_close_sequence_and_round_trips_through_a_real_xml_parser() -> None:
    """`]]>` embedded in the source text would otherwise prematurely close the CDATA section --
    verify a real XML parser reconstructs the exact original text from the escaped output,
    rather than just eyeballing the escaped string's shape."""
    original = "a]]>b"
    wrapped = cdata(original)
    assert wrapped == "<![CDATA[a]]]]><![CDATA[>b]]>"

    root = ElementTree.fromstring(f"<root>{wrapped}</root>")
    assert root.text == original
