"""Shared text helpers."""
import re

# Abbreviations that CONTINUE a sentence (a name or number follows). Sentence-final
# abbreviations like "etc." are deliberately absent: "…letters, etc. He was crowned" is two sentences.
CONTINUING = re.compile(r"(?:\b[A-Z]|\b(?:Mr|Mrs|Ms|Dr|St|Sr|Jr|Gen|Col|Maj|Capt|Lt|Rev|Hon|Prof|No|vs|c|ca|Vol|pp?))\.$")
NUMERIC = re.compile(r"\b(?:No|Vol|ca|c|pp?|vs|Nos|fol|ff)\.$")      # "ca. 1840", "No. 3", "Vol. 2"
BOUNDARY = re.compile(r"(?<=[.!?])\s+|(?<=[.!?][\"'”’)\]])\s+")   # also after a closing quote/bracket


PARAGRAPH = re.compile(r"\n[ \t]*\n")                              # blank line = hard boundary


def sentences(text: str) -> list[str]:
    """Split into sentences without cutting "Mr. Smith", "Martha E. Tyson" or "Dr. van der Meer"
    in half, without gluing a quoted sentence to the next one, and never joining across a
    paragraph break."""
    out = []
    for para in PARAGRAPH.split((text or "").strip()):
        first = len(out)
        for part in BOUNDARY.split(para.strip()):
            if len(out) > first and ((CONTINUING.search(out[-1]) and part[:1].isalpha()) or
                                     (NUMERIC.search(out[-1]) and part[:1].isdigit())):
                out[-1] += " " + part
            elif part:
                out.append(part)
    return out
