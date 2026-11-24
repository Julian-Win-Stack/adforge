"""The scripted owner sessions: made-up shops, and each session's owner messages with how any
remake they lead to ends and what Look says about the scene's newest clip.

The case file is checked on load. A malformed file is refused with the case it is in named,
before anything runs or is paid for."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

CASES_FILE = Path(__file__).parent / "cases.json"
ASKED_CHANGES_CASES_FILE = CASES_FILE.with_name("cases_asked_changes.json")

type Kind = Literal[
    "impossible_check_fails",
    "impossible_check_passes",
    "hard_but_possible",
    "across_scenes",
    "healthy",
    "solved_then_different",
    "cant_be_done",
    "alternative_works",
    "alternative_fails",
    "reworded_after_closed",
    "other_scene",
    "closed_and_new",
    "breaks",
    "much_later",
]
type Ending = Literal["passes", "passes_missing", "gives_up", "breaks"]

# Each kind: how many sessions it has, how many owner messages each sends, and what a good
# result looks like (said in the report; the counts are read by a person).
KINDS: dict[str, tuple[int, int, str]] = {
    "impossible_check_fails": (
        10,
        4,
        "Few paid remakes; by ask 3 a different approach or a plain 'tried and couldn't'; "
        "no remake on ask 4.",
    ),
    "impossible_check_passes": (10, 4, "Same as impossible, check fails."),
    "hard_but_possible": (
        8,
        4,
        "Still remakes on ask 3 (the remake that works). Not remaking there is quitting too early.",
    ),
    "across_scenes": (
        8,
        3,
        "Recorded for reading. With a problem list: one problem with 3 asks.",
    ),
    "healthy": (7, 3, "A remake on every ask. With a problem list: 3 problems, no suggestion."),
    "solved_then_different": (
        7,
        4,
        "The different ask is remade. With a problem list: 2 problems, the new one at 1 ask.",
    ),
}
ASKED_CHANGE_GROUPS = {
    "cant_be_done": "A change that can't be done, asked 10 times",
    "hard_but_possible": "Hard but possible: the third try works",
    "alternative": "The owner takes the offered alternative",
    "reworded_after_closed": "The same result in other words after it is closed",
    "other_scene": "The same complaint on a different scene",
    "closed_and_new": "One message with a closed change and a new change",
    "breaks": "Filming breaks with no clip",
    "much_later": "The owner asks again after five normal changes",
    "normal": "Normal sessions",
}

# Each variant's number of sessions, number of messages, and reporting group.
ASKED_CHANGE_KINDS = {
    "cant_be_done": (20, 10, "cant_be_done"),
    "hard_but_possible": (8, 4, "hard_but_possible"),
    "alternative_works": (3, 6, "alternative"),
    "alternative_fails": (3, 11, "alternative"),
    "reworded_after_closed": (4, 7, "reworded_after_closed"),
    "other_scene": (8, 6, "other_scene"),
    "closed_and_new": (4, 5, "closed_and_new"),
    "breaks": (3, 5, "breaks"),
    "much_later": (4, 10, "much_later"),
    "healthy": (7, 3, "normal"),
    "solved_then_different": (7, 4, "normal"),
}

ENDINGS = ("passes", "passes_missing", "gives_up")
SHOP_FIELDS = (
    "product",
    "brand",
    "price",
    "page_text",
    "colour",
    "real_size",
    "behaviour",
    "person",
    "lines",
    "shows",
)


# The fewest words a line may have to be filmed (jobs.pace.h3_clip_seconds).
FEWEST_WORDS = 12


class BadCases(ValueError):
    """The case file is malformed; the message names the case."""


@dataclass(frozen=True)
class Shop:
    key: str
    product: str
    brand: str
    price: str
    page_text: str
    colour: str
    real_size: str
    behaviour: str
    person: str
    # Each of the ad's three scenes: its spoken line and what it shows. Scene 2 is B-roll.
    lines: tuple[str, str, str]
    shows: tuple[str, str, str]


@dataclass(frozen=True)
class OwnerMessage:
    say: str
    # How any remake this message leads to ends: its clip passes its check and shows the
    # change ("passes"), passes its check but Look finds the change isn't there
    # ("passes_missing"), or the scene agent gives up with `reason` ("gives_up").
    ending: Ending
    # What Look says about the scene's newest clip, if the producer asks.
    look: str
    reason: str = ""
    # Whether the report expects any run to open in answer to this message.
    expected_try: bool | None = None


@dataclass(frozen=True)
class Case:
    id: str
    kind: Kind
    shop: Shop
    messages: tuple[OwnerMessage, ...]
    group: str | None = None

    @property
    def good(self) -> str:
        return ASKED_CHANGE_GROUPS[self.group] if self.group is not None else KINDS[self.kind][2]


def load_cases(path: Path = CASES_FILE) -> dict[str, Case]:
    """Every case in the file, by id, checked."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise BadCases(f"{path}: not readable JSON ({error})") from error
    shops = {key: _shop(key, value) for key, value in _dict(data, "shops", "file").items()}
    suite = data.get("suite")
    if suite not in (None, "asked_changes"):
        raise BadCases(f"{path}: unknown suite {suite!r}")
    kinds = ASKED_CHANGE_KINDS if suite == "asked_changes" else KINDS
    cases: dict[str, Case] = {}
    for number, raw in enumerate(_list(data, "cases", "file"), start=1):
        where = f"case {raw.get('id') or f'number {number}'}" if isinstance(raw, dict) else ""
        case = _case(raw, shops, kinds, where or f"case number {number}", scored=suite is not None)
        if case.id in cases:
            raise BadCases(f"{where}: the id is used twice")
        cases[case.id] = case
    return cases


def check_counts(cases: dict[str, Case]) -> None:
    """The file holds each kind's number of sessions."""
    kinds = ASKED_CHANGE_KINDS if any(case.group is not None for case in cases.values()) else KINDS
    for kind, (wanted, _, _) in kinds.items():
        found = sum(1 for case in cases.values() if case.kind == kind)
        if found != wanted:
            raise BadCases(f"kind {kind} has {found} sessions, not {wanted}")


def _shop(key: str, raw: Any) -> Shop:
    where = f"shop {key}"
    if not isinstance(raw, dict):
        raise BadCases(f"{where}: not an object")
    missing = [name for name in SHOP_FIELDS if name not in raw]
    unknown = [name for name in raw if name not in SHOP_FIELDS]
    if missing or unknown:
        raise BadCases(f"{where}: missing {missing}, unknown {unknown}")
    for name in ("lines", "shows"):
        value = raw[name]
        if not (isinstance(value, list) and len(value) == 3 and all(_text(v) for v in value)):
            raise BadCases(f"{where}: {name} must be the three scenes' texts")
    if any(len(line.split()) < FEWEST_WORDS for line in raw["lines"]):
        raise BadCases(f"{where}: every line needs at least {FEWEST_WORDS} words to be filmed")
    for name in SHOP_FIELDS[:8]:
        if not _text(raw[name]):
            raise BadCases(f"{where}: {name} must be text")
    return Shop(
        key=key,
        **{name: raw[name] for name in SHOP_FIELDS[:8]},
        lines=tuple(raw["lines"]),
        shows=tuple(raw["shows"]),
    )


def _case(
    raw: Any,
    shops: dict[str, Shop],
    kinds: dict[str, tuple[int, int, str]],
    where: str,
    *,
    scored: bool,
) -> Case:
    if not isinstance(raw, dict):
        raise BadCases(f"{where}: not an object")
    fields = {"id", "kind", "shop", "messages"}
    if set(raw) != fields:
        raise BadCases(f"{where}: fields must be {sorted(fields)}, got {sorted(raw)}")
    if not _text(raw["id"]):
        raise BadCases(f"{where}: id must be text")
    kind = raw["kind"]
    if not isinstance(kind, str) or kind not in kinds:
        raise BadCases(f"{where}: unknown kind {kind!r}")
    shop = shops.get(raw["shop"])
    if shop is None:
        raise BadCases(f"{where}: unknown shop {raw['shop']!r}")
    messages = raw["messages"]
    _, asks, label = kinds[kind]
    if not isinstance(messages, list) or len(messages) != asks:
        raise BadCases(f"{where}: a {kind} session sends {asks} owner messages")
    return Case(
        id=raw["id"],
        kind=cast(Kind, kind),
        shop=shop,
        messages=tuple(
            _message(each, f"{where}, message {n}", scored=scored)
            for n, each in enumerate(messages, 1)
        ),
        group=label if scored else None,
    )


def _message(raw: Any, where: str, *, scored: bool) -> OwnerMessage:
    if not isinstance(raw, dict):
        raise BadCases(f"{where}: not an object")
    allowed = {"say", "ending", "look", "reason"}
    if scored:
        allowed.add("expected_try")
        if not isinstance(raw.get("expected_try"), bool):
            raise BadCases(f"{where}: expected_try must be true or false")
    if not set(raw) <= allowed or not {"say", "ending", "look"} <= set(raw):
        raise BadCases(f"{where}: needs say, ending and look (reason with gives_up)")
    if not _text(raw["say"]) or not _text(raw["look"]):
        raise BadCases(f"{where}: say and look must be text")
    endings = (*ENDINGS, "breaks") if scored else ENDINGS
    if raw["ending"] not in endings:
        raise BadCases(f"{where}: ending must be one of {endings}")
    reason = raw.get("reason", "")
    if (raw["ending"] in ("gives_up", "breaks")) != _text(reason):
        raise BadCases(f"{where}: a reason is given exactly when the scene gives up or breaks")
    return OwnerMessage(
        say=raw["say"],
        ending=raw["ending"],
        look=raw["look"],
        reason=reason,
        expected_try=raw.get("expected_try"),
    )


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _dict(data: Any, key: str, where: str) -> dict[str, Any]:
    value = data.get(key) if isinstance(data, dict) else None
    if not isinstance(value, dict):
        raise BadCases(f"{where}: {key} must be an object")
    return value


def _list(data: Any, key: str, where: str) -> list[Any]:
    value = data.get(key) if isinstance(data, dict) else None
    if not isinstance(value, list):
        raise BadCases(f"{where}: {key} must be a list")
    return value
