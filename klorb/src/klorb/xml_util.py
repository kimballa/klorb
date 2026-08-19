# © Copyright 2026 Aaron Kimball
"""Small helpers for embedding freeform text inside XML sent to a model.
"""


def cdata(text: str) -> str:
    """Wrap `text` in an XML `CDATA` section, splitting any embedded literal `]]>` into
    consecutive `CDATA` sections."""
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"
