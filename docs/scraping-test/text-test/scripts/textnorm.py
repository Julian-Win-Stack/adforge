"""Shared text matching for the text test (same rules as screen-test/scripts/score_text.py)."""
import re


def norm(s: str) -> str:
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s.casefold())).strip()


def found(sentence: str, text: str) -> bool:
    n = norm(sentence)
    if len(n) < 12: return False
    if n in text: return True
    w = n.split()
    grams = [" ".join(w[i:i + 3]) for i in range(len(w) - 2)]
    return bool(grams) and sum(g in text for g in grams) / len(grams) >= 0.8


def sentences(text: str, cap: int = 300) -> list[str]:
    """Split into sentences; long runs without full stops (lists joined by spaces) are cut near
    `cap` characters at a space. Very short bits are dropped."""
    out = []
    for s in re.split(r"(?<=[.!?])\s+|\n+", text):
        s = s.strip()
        while len(s) > cap:
            cut = s.rfind(" ", 0, cap)
            cut = cut if cut > cap // 2 else cap
            out.append(s[:cut].strip()); s = s[cut:].strip()
        if len(norm(s)) >= 12: out.append(s)
    return [s for s in out if len(norm(s)) >= 12]
