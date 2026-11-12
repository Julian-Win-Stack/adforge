"""The approved eval questions, extended by questions from Julian's later complaints."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from evals.cases import FOLDER, load

type Stage = Literal["page", "plan", "pictures", "clips", "ad", "note"]
type AnsweredBy = Literal["code", "julian"]
STAGES: tuple[Stage, ...] = ("page", "plan", "pictures", "clips", "ad", "note")


@dataclass(frozen=True)
class Question:
    id: str
    stage: Stage
    answered_by: AnsweredBy
    words: str

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
    Question("page_facts_this_product", "page", "julian", "Only this product's facts are kept"),
    Question("plan_has_demo", "plan", "code", "At least one demo scene"),
    Question("plan_names_brand", "plan", "code", "The brand or product name is in the script"),
    Question(
        "plan_fits_length",
        "plan",
        "code",
        "The script fits the ad length (35 s, or the owner's target)",
    ),
    Question(
        "plan_claims_backed",
        "plan",
        "julian",
        "Every claim, number and price comes from the page or the owner",
    ),
    Question("plan_real_use", "plan", "julian", "The product is used the way the page shows"),
    Question(
        "viewer_knows_product",
        "plan",
        "julian",
        "The viewer can tell what the product is (from his past complaints, e.g. CATAN)",
    ),
    Question(
        "picture_real_product",
        "pictures",
        "julian",
        "It's the real product (shape, parts, colours)",
    ),
    Question("picture_possible", "pictures", "julian", "Nothing in the picture is impossible"),
    Question(
        "picture_looks_natural",
        "pictures",
        "julian",
        "The presenter doesn't look pasted onto the background "
        "(from his past complaints, e.g. Ridge)",
    ),
    Question("clip_real_product", "clips", "julian", "It's the real product"),
    Question("clip_possible", "clips", "julian", "Nothing bends or floats in an impossible way"),
    Question(
        "clip_substance_real",
        "clips",
        "julian",
        "Foam, cream or liquid behaves like the real thing",
    ),
    Question(
        "clip_camera_natural",
        "clips",
        "julian",
        "The camera moves naturally, not in to the product and back out "
        "(from his past complaints, e.g. Hatch, Furbo)",
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
    Question(
        "consistent_across_scenes",
        "ad",
        "julian",
        "The product and props stay the same in every scene "
        "(from his past complaints, e.g. Gorilla Glue)",
    ),
    Question(
        "presenter_real_person",
        "ad",
        "julian",
        "The presenter feels like a real person (existing case file, same question)",
    ),
    Question("note_done", "note", "julian", "The note was done"),
    Question(
        "note_untouched_same",
        "note",
        "code",
        "Scenes the note didn't touch keep exactly the same clip",
    ),
)


def all_questions(*, cases_dir: Path = FOLDER) -> list[Question]:
    questions = list(QUESTIONS)
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
