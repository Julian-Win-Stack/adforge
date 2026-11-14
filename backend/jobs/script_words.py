"""Canonical script words shared by planning, evals and leading-word trimming."""

import difflib
import re
from collections.abc import Sequence


def leading_words(line: str, heard: Sequence[str]) -> int:
    """How many original words precede the line, allowing substitutions within it.

    Insertions or omissions within the line are not a leading false start. Normalized
    tokens keep their original word index so a written number's expansion cannot move
    the cut into a word.
    """
    tokens = [(token, at) for at, word in enumerate(heard) for token in tidy(word).split()]
    written = tidy(line).split()
    if not tokens or not written:
        return 0
    said = [token for token, _ in tokens]
    if len(said) > len(written) and said[-len(written) :] == written:
        return tokens[len(said) - len(written)][1]
    changes = difflib.SequenceMatcher(None, written, said, autojunk=False).get_opcodes()
    kind, _, _, _, past = changes[0]
    if kind != "insert" or past >= len(tokens):
        return 0
    rest = changes[1:]
    if not rest or rest[0][0] != "equal" or any(kind in {"insert", "delete"} for kind, *_ in rest):
        return 0
    return tokens[past][1]


_SMALL = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen"
).split()
_TENS = "zero ten twenty thirty forty fifty sixty seventy eighty ninety".split()


def _number_words(number: int) -> str:
    if number < 20:
        return _SMALL[number]
    if number < 100:
        tens, ones = divmod(number, 10)
        return _TENS[tens] + (f" {_SMALL[ones]}" if ones else "")
    for size, name in (
        (10**12, "trillion"),
        (10**9, "billion"),
        (10**6, "million"),
        (1000, "thousand"),
        (100, "hundred"),
    ):
        if number >= size:
            whole, rest = divmod(number, size)
            return f"{_number_words(whole)} {name}" + (f" {_number_words(rest)}" if rest else "")
    raise AssertionError("a nonnegative number always has words")


def tidy(text: str) -> str:
    """Canonical words, preserving names, quantities and claims."""

    def number(match: re.Match[str]) -> str:
        digits = match.group().replace(",", "")
        whole, dot, fraction = digits.partition(".")
        return _number_words(int(whole or "0")) + (
            " point " + " ".join(_SMALL[int(digit)] for digit in fraction) if dot else ""
        )

    # Only letter-to-letter hyphens are mere writing differences. Numeric operators,
    # fractions and ratios must survive so different quantities stay different.
    text = re.sub(r"(?<=[^\W\d_])[-–—](?=[^\W\d_])", " ", text)
    text = re.sub(r"(?<!\w)[−-](?=[$£€¥₹]?(?:\d|\.\d))", "minus ", text)
    for symbol, unit in (
        ("$", "dollars"),
        ("£", "pounds"),
        ("€", "euros"),
        ("¥", "yen"),
        ("₹", "rupees"),
    ):
        text = re.sub(re.escape(symbol) + r"(\d+(?:,\d{3})*(?:\.\d+)?)", rf"\1 {unit}", text)
    text = re.sub(r"\d+(?:,\d{3})*(?:\.\d+)?|\.\d+", number, text)
    text = text.replace("%", " percent ")
    return " ".join(re.findall(r"\w+|[^\w\s.,!?;\"'()\[\]{}…]", text.casefold()))
