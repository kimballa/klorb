# © Copyright 2026 Aaron Kimball
"""Small helpers for embedding freeform text inside XML sent to a model, shared by any
caller that needs to hand the model untrusted or arbitrary text without letting it break out
of the surrounding tag structure.
"""


def cdata(text: str) -> str:
    """Wrap `text` in an XML `CDATA` section, splitting around any embedded literal `]]>` (which
    would otherwise prematurely close the section) into consecutive `CDATA` sections instead."""
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"
