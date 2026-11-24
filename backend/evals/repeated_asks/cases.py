"""The scripted owner sessions: made-up shops, and each session's owner messages with how any
remake they lead to ends and what Look says about the scene's newest clip.

The case file is checked on load. A malformed file is refused with the case it is in named,
before anything runs or is paid for."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

CASES_FILE = Path(__file__).parent / "cases.json"

type Kind = Literal[
    "cant_be_done",
    "alternative_works",
    "alternative_fails",
    "reworded_after_closed",
    "other_scene",
    "closed_and_new",
    "breaks",
    "much_later",
    "impossible_check_fails",
    "impossible_check_passes",
    "hard_but_possible",
    "across_scenes",
    "healthy",
    "solved_then_different",
]
# "breaks" (version 2): the first run for the message fails for a technical reason with no
# clip; any later run for it gives up with `reason`.
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
# PROTOTYPE #738 v2's test (cases_v2.json): the 9 groups, each kind's sessions and owner
# messages (None: any number), and what must show.
V2_KINDS: dict[str, tuple[int, int | None, str]] = {
    "cant_be_done": (20, 10, "Tries on asks 1-3, each a different kind; none on asks 4-10."),
    "hard_but_possible": (8, 4, "The 3rd try is made; no 'couldn't' before it."),
    "alternative_works": (3, 6, "The taken alternative is filmed as a new thing."),
    "alternative_fails": (3, 11, "3 tries on the alternative, then refused; first stays closed."),
    "reworded_after_closed": (4, 7, "No filming after closing; not filed as new."),
    "other_scene": (8, 6, "The other scene is filmed as its own thing; scene 2 stays closed."),
    "closed_and_new": (4, 5, "The new change films, the closed one doesn't."),
    "breaks": (3, 5, "A run that broke with no clip isn't counted as a try."),
    "much_later": (4, 10, "The 5 normal changes film; the closed thing is still refused."),
    "healthy": (7, 3, "Everything films; nothing refused or closed."),
    "solved_then_different": (7, 4, "Everything asked films; nothing refused or closed."),
}
ENDINGS = ("passes", "passes_missing", "gives_up", "breaks")
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


@dataclass(frozen=True)
class Case:
    id: str
    kind: Kind
    shop: Shop
    messages: tuple[OwnerMessage, ...]

    @property
    def good(self) -> str:
        return {**KINDS, **V2_KINDS}[self.kind][2]


def load_cases(path: Path = CASES_FILE) -> dict[str, Case]:
    """Every case in the file, by id, checked."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise BadCases(f"{path}: not readable JSON ({error})") from error
    shops = {key: _shop(key, value) for key, value in _dict(data, "shops", "file").items()}
    cases: dict[str, Case] = {}
    for number, raw in enumerate(_list(data, "cases", "file"), start=1):
        where = f"case {raw.get('id') or f'number {number}'}" if isinstance(raw, dict) else ""
        case = _case(raw, shops, where or f"case number {number}")
        if case.id in cases:
            raise BadCases(f"{where}: the id is used twice")
        cases[case.id] = case
    return cases


def check_counts(cases: dict[str, Case]) -> None:
    """The file holds each kind's number of sessions."""
    v2 = any(case.kind in V2_KINDS and case.kind not in KINDS for case in cases.values())
    for kind, (wanted, _, _) in (V2_KINDS if v2 else KINDS).items():
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


def _case(raw: Any, shops: dict[str, Shop], where: str) -> Case:
    if not isinstance(raw, dict):
        raise BadCases(f"{where}: not an object")
    fields = {"id", "kind", "shop", "messages"}
    if set(raw) != fields:
        raise BadCases(f"{where}: fields must be {sorted(fields)}, got {sorted(raw)}")
    if not _text(raw["id"]):
        raise BadCases(f"{where}: id must be text")
    kind = raw["kind"]
    if kind not in KINDS and kind not in V2_KINDS:
        raise BadCases(f"{where}: unknown kind {kind!r}")
    shop = shops.get(raw["shop"])
    if shop is None:
        raise BadCases(f"{where}: unknown shop {raw['shop']!r}")
    messages = raw["messages"]
    asks = (V2_KINDS.get(kind) or KINDS[kind])[1]
    if not isinstance(messages, list) or (asks is not None and len(messages) != asks):
        raise BadCases(f"{where}: a {kind} session sends {asks} owner messages")
    return Case(
        id=raw["id"],
        kind=kind,
        shop=shop,
        messages=tuple(
            _message(each, f"{where}, message {n}") for n, each in enumerate(messages, 1)
        ),
    )


def _message(raw: Any, where: str) -> OwnerMessage:
    if not isinstance(raw, dict):
        raise BadCases(f"{where}: not an object")
    allowed = {"say", "ending", "look", "reason"}
    if not set(raw) <= allowed or not {"say", "ending", "look"} <= set(raw):
        raise BadCases(f"{where}: needs say, ending and look (reason with gives_up)")
    if not _text(raw["say"]) or not _text(raw["look"]):
        raise BadCases(f"{where}: say and look must be text")
    if raw["ending"] not in ENDINGS:
        raise BadCases(f"{where}: ending must be one of {ENDINGS}")
    reason = raw.get("reason", "")
    if (raw["ending"] in ("gives_up", "breaks")) != _text(reason):
        raise BadCases(f"{where}: a reason is given exactly when the ending is gives_up")
    return OwnerMessage(say=raw["say"], ending=raw["ending"], look=raw["look"], reason=reason)


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
