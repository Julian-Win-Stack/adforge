"""The code and judge questions, and questions named from Julian's own complaints.

Julian answers only questions named from his own words: filing starts one the first time a
complaint fits none, from a case file's first case. No Claude-written question is his."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from evals.cases import FOLDER, load

type Stage = Literal["page", "plan", "pictures", "clips", "ad", "note"]
type AnsweredBy = Literal["code", "julian", "judge"]
STAGES: tuple[Stage, ...] = ("page", "plan", "pictures", "clips", "ad", "note")


@dataclass(frozen=True)
class Question:
    id: str
    stage: Stage
    answered_by: AnsweredBy
    words: str
    unit: str = ""
    frames_per_second: int | None = None
    page_photos: bool = True

    @property
    def case_file(self) -> Path:
        return FOLDER / f"{self.id}.jsonl"


QUESTIONS = (
    Question(
        "page_photos_this_product",
        "page",
        "code",
        "Only this product's photos are treated as the product",
    ),
    Question("plan_has_demo", "plan", "code", "At least one demo scene"),
    Question("plan_names_brand", "plan", "code", "The brand or product name is in the script"),
    Question(
        "plan_fits_length",
        "plan",
        "code",
        "The script fits the ad length (35 s, or the owner's target)",
    ),
    Question("clip_words_match", "clips", "code", "The words heard match the scene's line"),
    Question("ad_finishes", "ad", "code", "A finished ad is made"),
    Question(
        "ad_fits_length",
        "ad",
        "code",
        "The finished file runs 35 s or less (or the owner's target + 2 s)",
    ),
    Question("ad_end_card", "ad", "code", "End card of 1.5 s or more, with a photo"),
    Question("ad_brand_heard", "ad", "code", "The brand or product name is heard"),
    Question("ad_last_words", "ad", "code", "No scene's last words are cut off"),
    Question("ad_no_dead_air", "ad", "code", "No silence longer than 1.0 s before the end card"),
    Question("note_new_ad", "note", "code", "A new finished ad is made after the note"),
    Question(
        "note_right_scenes",
        "note",
        "code",
        "Every scene the note is about is remade with a new clip",
    ),
    Question(
        "note_untouched_same",
        "note",
        "code",
        "Scenes the note didn't touch keep exactly the same clip",
    ),
)


JUDGE_QUESTIONS = (
    Question("P1", "pictures", "judge", "Real product", "pictures"),
    Question("P4", "pictures", "judge", "Staged like real life", "pictures"),
    Question("C5", "clips", "judge", "Real product, same size the whole clip", "clips", 10),
    Question("C6", "clips", "judge", "Nothing impossible", "clips", 10),
    Question("C7", "clips", "judge", "Used the right way, on the right target", "clips", 10),
    Question("C8", "clips", "judge", "Cream or liquid behaves like the real thing", "clips", 10),
    Question("C9", "clips", "judge", "Camera moves naturally", "clips", 10),
    Question("C10", "clips", "judge", "Staged like real life", "clips", 10),
    Question("C13", "clips", "judge", "The holding hand is a real hand", "clips", 5, False),
    Question(
        "C14", "clips", "judge", "The action really works and its result stays", "clips", 5, False
    ),
    Question("A11", "ad", "judge", "Same product in every scene", "ads"),
)


# Checks of a whole note fix, asked outside the app on note runs (#695). They are scored
# beside the eval judge's questions but are not asked on first-time ads.
NOTE_QUESTIONS = (
    Question("N1", "note", "judge", "The change the owner asked for was made", "notes"),
)


def judge_questions(stage: Stage | None = None) -> list[Question]:
    return [q for q in JUDGE_QUESTIONS if stage is None or q.stage == stage]


def note_questions() -> list[Question]:
    return list(NOTE_QUESTIONS)


def all_questions(*, cases_dir: Path = FOLDER) -> list[Question]:
    questions = [*QUESTIONS, *JUDGE_QUESTIONS, *NOTE_QUESTIONS]
    known = {q.id for q in questions}
    for path in sorted(cases_dir.glob("*.jsonl")):
        if path.stem in known:
            continue
        cases = load(path)
        if not cases:
            continue
        handoff = cases[0].inputs["handoff"]
        stage, words = handoff.get("stage"), handoff.get("question")
        # Older evals have neither field. Only regression question metadata opts in.
        if stage is None or words is None:
            continue
        if stage not in STAGES or not isinstance(words, str) or not words.strip():
            raise ValueError(f"{path}: first case needs a valid stage and non-empty question")
        questions.append(Question(path.stem, cast(Stage, stage), "julian", words))
    return questions


def for_stage(stage: Stage, *, cases_dir: Path = FOLDER) -> list[Question]:
    return [q for q in all_questions(cases_dir=cases_dir) if q.stage == stage]


def julian_questions(stage: Stage | None = None, *, cases_dir: Path = FOLDER) -> list[Question]:
    return [
        q
        for q in all_questions(cases_dir=cases_dir)
        if q.answered_by == "julian" and (stage is None or q.stage == stage)
    ]


def code_questions(stage: Stage | None = None, *, cases_dir: Path = FOLDER) -> list[Question]:
    return [
        q
        for q in all_questions(cases_dir=cases_dir)
        if q.answered_by == "code" and (stage is None or q.stage == stage)
    ]
