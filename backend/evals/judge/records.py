"""Shared JSON contracts for the judge's answers, human taps and case inputs (#675)."""

import re
from collections.abc import Mapping
from typing import Any, Literal, NotRequired, Self, TypedDict

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from evals.regression.questions import Stage, judge_questions

type JudgeVerdict = Literal["pass", "fail", "doesnt_apply"]
type Tap = Literal["not_a_flaw", "real_flaw_ship", "real_flaw_dont_ship"]
TAP_WORDS: dict[Tap, str] = {
    "not_a_flaw": "not a flaw",
    "real_flaw_ship": "real flaw, fine to ship",
    "real_flaw_dont_ship": "real flaw, don't ship",
}
ITEM_QUESTIONS = {"pictures": "judge_picture_item", "clips": "judge_clip_item"}


class JudgeCaseHandoff(TypedDict):
    stage: Stage
    item: str
    line: str
    shows: str
    product_name: str
    product_text: str
    page_photo_labels: list[str]
    question: NotRequired[str]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class HandRow(Record):
    seconds: str
    wrist_side: str
    knuckles_side: str
    fingertips_point: str
    thumb: str
    possible: bool


class PhotoSide(Record):
    photo: str
    side: str
    parts: str


class ShownSide(Record):
    side: str
    when: str
    parts: str
    same_side_photo: str
    differences: str


class JudgeAsk(Record):
    """One paid ask, kept even when the counted verdict later changes."""

    verdict: JudgeVerdict
    sees: str
    when: str
    on_what: Literal["product", "hand", "target", "presenter", "background", "nothing"]
    model: str
    at: str
    input_tokens: int = Field(ge=0)
    cached_tokens: int = Field(ge=0)
    cache_write_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0, allow_inf_nan=False)
    rows: list[HandRow] | None = None
    photo_sides: list[PhotoSide] | None = None
    shown_sides: list[ShownSide] | None = None


class FailedAsk(Record):
    """A billed refusal or unusable reply, never counted as a verdict."""

    error: str
    model: str
    at: str
    input_tokens: int = Field(ge=0)
    cached_tokens: int = Field(ge=0)
    cache_write_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0, allow_inf_nan=False)


class JudgeAnswer(Record):
    judge: str = Field(min_length=1)
    verdict: JudgeVerdict | None = None
    asks: list[JudgeAsk]
    error: str | None = None
    failed_asks: list[FailedAsk] = Field(default_factory=list)
    history: list[JudgeAnswer] = Field(default_factory=list)

    @model_validator(mode="after")
    def error_is_unjudged(self) -> Self:
        if self.error and self.verdict is not None:
            raise ValueError("An errored item must be unjudged (no verdict)")
        return self


def answer_cost(answer: Mapping[str, Any]) -> float:
    """Every paid attempt, including errors and earlier versions of this question."""
    return sum(
        float(ask["cost_usd"]) for ask in [*answer.get("asks", []), *answer.get("failed_asks", [])]
    ) + sum(answer_cost(older) for older in answer.get("history", []))


class JudgeTap(Record):
    tap: Tap
    note: str
    at: str


type JudgeAnswers = dict[str, dict[str, dict[str, dict[str, Any]]]]
_ANSWERS = TypeAdapter(dict[str, dict[str, dict[str, JudgeAnswer]]])
_TAPS = TypeAdapter(dict[str, JudgeTap])


def validate_answers(value: Any) -> JudgeAnswers:
    _ANSWERS.validate_python(value, strict=True)
    questions = {q.id: q for q in judge_questions()}
    for scenario, answers in value.items():
        if not isinstance(scenario, str) or not scenario:
            raise ValueError("Judge answers need a scenario")
        for question, items in answers.items():
            if question not in questions:
                raise ValueError(f"Unknown judge question: {question!r}")
            for item in items:
                _check_item(questions[question].stage, item)
    return dict(value)


def validate_taps(value: Any) -> dict[str, dict[str, Any]]:
    _TAPS.validate_python(value, strict=True)
    for doc_id in value:
        if parse_tap_doc_id(doc_id) is None:
            raise ValueError(f"Unknown judge tap document: {doc_id!r}")
    return dict(value)


def _check_item(stage: Stage, item: str) -> None:
    if (stage == "ad" and item == "ad") or (
        stage in ("pictures", "clips") and re.fullmatch(r"s[1-9]\d*", item)
    ):
        return
    raise ValueError(f"Unknown judge item {item!r} at stage {stage!r}")


def tap_doc_id(scenario: str, stage: Stage, item: str, question: str) -> str:
    """The grading page's existing doc scheme, with the judge question appended."""
    if not scenario or "__" in scenario:
        raise ValueError(f"Unknown scenario: {scenario!r}")
    known = {q.id: q.stage for q in judge_questions()}
    if known.get(question) != stage:
        raise ValueError(f"Unknown judge question {question!r} at stage {stage!r}")
    _check_item(stage, item)
    media = {"pictures": "picture", "clips": "clip"}
    suffix = f"{item}__{media[stage]}" if stage in media else item
    return f"{scenario}__{suffix}__{question}"


def parse_tap_doc_id(doc_id: str) -> tuple[str, Stage, str, str] | None:
    parts = doc_id.split("__")
    if len(parts) == 4 and parts[2] in ("picture", "clip"):
        scenario, item, media, question = parts
        stage: Stage = "pictures" if media == "picture" else "clips"
    elif len(parts) == 3 and parts[1] == "ad":
        scenario, item, question = parts
        stage = "ad"
    else:
        return None
    try:
        if tap_doc_id(scenario, stage, item, question) != doc_id:
            return None
    except ValueError:
        return None
    return scenario, stage, item, question


def judge_taps(grades: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Select judge documents; malformed judge taps raise rather than disappearing."""
    selected = {key: value for key, value in grades.items() if parse_tap_doc_id(key) is not None}
    return validate_taps(selected)
