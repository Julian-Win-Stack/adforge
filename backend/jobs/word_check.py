"""Did a clip say its checked line? Writing differences are free; meaning goes to a model."""

import difflib
import json
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Literal

from pydantic import BaseModel, Field

from adforge.retry import OutsideServiceDown
from chat.models import Message
from gateway.gateway import call_model
from gateway.types import AnswerTookTooLong, BlockedBySafetyFilter, Handoff, UnusableReply

from . import notices
from .models import Job

PURPOSE = "check_words"
WORD_CHECK = notices.Kind("word_check", for_owner=False, failure=False)

QUESTION = """Compare the checked line with the words heard in a clip. Decide whether the clip \
said the checked line faithfully. Fail a different, missing or extra name, number, price or \
claim, including words cut off at the end. Also fail any missing word or extra words. \
Pass a writing difference such as a number written in digits but spoken in words (exact). \
Pass a small slip a listener would not notice, such as an inflection that leaves the claim \
unchanged (small_slip). Pass the product's own name pronounced a little wrong (product_name), \
but fail another name. For product_name, copy the misheard name into name_heard and its \
correct spelling from the checked line into name_written; change no other words. The \
product_name field identifies which name is the product's own. Judge only these two texts, \
not whether the checked line itself is true. Give pass or fail, the rule for a pass and a \
short reason."""


class Words(Handoff):
    line: str
    said: str
    product_name: str


class Answer(BaseModel):
    verdict: Literal["pass", "fail"]
    reason: str = Field(min_length=1, description="Why the words pass or fail, in plain words.")
    rule: Literal["exact", "small_slip", "product_name"] = "exact"
    name_heard: str = ""
    name_written: str = ""


@dataclass(frozen=True)
class Result:
    line: str
    said: str | None
    verdict: Literal["pass", "fail", "unchecked"]
    reason: str
    settled_by: Literal["code", "model"]
    rule: Literal["exact", "small_slip", "product_name"] | None = None
    caption_line: str | None = None


def leading_words(line: str, heard: Sequence[str]) -> int:
    """How many original words precede the line, allowing substitutions for its check.

    Insertions or omissions within the line are not a leading false start. Normalized
    tokens keep their original word index so a written number's expansion cannot move
    the cut into a word.
    """
    tokens = [(token, at) for at, word in enumerate(heard) for token in tidy(word).split()]
    if not tokens or not tidy(line):
        return 0
    changes = difflib.SequenceMatcher(
        None, tidy(line).split(), [token for token, _ in tokens], autojunk=False
    ).get_opcodes()
    kind, _, _, _, past = changes[0]
    if kind != "insert" or past >= len(tokens):
        return 0
    rest = changes[1:]
    if not rest or rest[0][0] != "equal" or any(kind in {"insert", "delete"} for kind, *_ in rest):
        return 0
    return tokens[past][1]


def caption_words(line: str, said: str, product_name: str, answer: Answer) -> str:
    """Keep heard slips, correcting only a product-name span copied from the checked line."""
    if answer.verdict != "pass" or answer.rule == "exact":
        return line
    if answer.rule == "small_slip":
        return said
    heard, written = answer.name_heard, answer.name_written
    if (
        not heard.strip()
        or not written.strip()
        or written not in line
        or not product_name
        or re.search(rf"(?<!\w){re.escape(tidy(written))}(?!\w)", tidy(product_name)) is None
        or re.search(rf"(?<!\w){re.escape(heard)}(?!\w)", said) is None
    ):
        raise ValueError("The product-name correction did not identify the heard and written name.")
    return re.sub(rf"(?<!\w){re.escape(heard)}(?!\w)", lambda _: written, said)


def check(job: Job, line: str, said: str | None) -> Result:
    """Check the clip's words, recording exactly one nonfailure notice for this check.

    No transcript or no usable answer keeps the clip unchecked; filming decides how to
    tell the owner. Only a textual difference reaches the model.
    """
    if said is None or not said.strip():
        result = Result(line, said, "unchecked", "The clip has no transcript to check.", "code")
    elif tidy(line) == tidy(said):
        result = Result(
            line, said, "pass", "The tidied words match exactly.", "code", "exact", line
        )
    else:
        try:
            answer = call_model(
                job=job,
                purpose=PURPOSE,
                instructions=QUESTION,
                handoff=Words(line=line, said=said, product_name=job.product_name),
                output=Answer,
                pay_once=True,
            )
            result = Result(
                line,
                said,
                answer.verdict,
                answer.reason,
                "model",
                answer.rule if answer.verdict == "pass" else None,
                caption_words(line, said, job.product_name, answer),
            )
        except (
            OutsideServiceDown,
            AnswerTookTooLong,
            BlockedBySafetyFilter,
            UnusableReply,
            ValueError,
        ) as error:
            result = Result(
                line, said, "unchecked", f"The word check gave no answer: {error}", "model"
            )
    notices.post_notice(job, WORD_CHECK, json.dumps(asdict(result)), Message.Level.INFO)
    return result


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
    """Canonical words, preserving names, quantities and claims for the meaning check."""

    def number(match: re.Match[str]) -> str:
        digits = match.group().replace(",", "")
        whole, dot, fraction = digits.partition(".")
        return _number_words(int(whole or "0")) + (
            " point " + " ".join(_SMALL[int(digit)] for digit in fraction) if dot else ""
        )

    # Only letter-to-letter hyphens are mere writing differences. Numeric operators,
    # fractions and ratios must survive so a changed quantity reaches the model.
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
