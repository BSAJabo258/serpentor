"""Shared text helpers."""
import re

ABBREV = re.compile(r"(?:\b[A-Z]|\b(?:Mr|Mrs|Ms|Dr|St|Sr|Jr|Gen|Col|Maj|Capt|Lt|Rev|Hon|Prof|No|vs|etc|c|ca|Vol|pp?))\.$")


def sentences(text: str) -> list[str]:
    """Split on sentence ends, but not after initials or common abbreviations
    ("Martha E. Tyson", "Mr. Smith"), so a sentence is never cut in half."""
    out = []
    for part in re.split(r"(?<=[.!?])\s+", (text or "").strip()):
        if out and ABBREV.search(out[-1]):
            out[-1] += " " + part
        elif part:
            out.append(part)
    return out
