"""Document text is DATA, never instructions.

`scan` flags text that tries to steer an AI (hidden instructions in PDFs, web pages,
OCR'd material). `fence` wraps passages so the model sees them as quoted evidence.
Flagged sources are still kept — the attempt itself can be evidence.
"""
import re

PATTERNS = [
    ("override", r"\b(ignore|disregard|forget)\b.{0,40}\b(previous|prior|above|all)\b.{0,20}\b(instructions?|rules|prompts?)\b"),
    ("role_hijack", r"\b(you are now|act as|pretend to be|new system prompt|system:)\b"),
    ("exfiltrate", r"\b(send|upload|post|email|forward)\b.{0,60}\b(https?://|api key|password|credentials|database)\b"),
    ("suppress", r"\b(do not|don't|never)\b.{0,30}\b(mention|cite|report|reveal|tell)\b.{0,40}\b(user|this|source|document)\b"),
    ("verdict_injection", r"\b(conclude|state|report)\s+that\b.{0,80}\b(is (false|true|fake|proven))\b"),
    ("hidden_markup", r"<\s*(script|iframe)\b|​|⁠"),
]
_COMPILED = [(n, re.compile(p, re.I | re.S)) for n, p in PATTERNS]


def scan(text: str) -> list[dict]:
    hits = []
    for name, rx in _COMPILED:
        for m in rx.finditer(text or ""):
            hits.append({"rule": name, "excerpt": text[max(0, m.start() - 20): m.end() + 20][:200]})
    return hits


def fence(passage_id: str, text: str) -> str:
    clean = (text or "").replace("<<<", "‹‹‹").replace(">>>", "›››")
    return f"<<<EVIDENCE {passage_id}\n{clean}\nEND {passage_id}>>>"
