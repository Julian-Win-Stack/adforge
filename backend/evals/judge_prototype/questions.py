"""The 12 eval questions (spec #644): which items each one is asked of, on which model, and
its prompt, read from `questions/<id>.md`. Every call of one item shares the same instructions
(`questions/_judge.md`) and pictures; only the question, sent last, differs, so Azure's prompt
cache can reuse the rest across an item's calls."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FOLDER = Path(__file__).parent / "questions"
SHARED = FOLDER / "_judge.md"

# Frames a second each clip is shown at: 10 to Azure as pictures (the app's PRODUCT check),
# 5 to Gemini as video with sound.
CLIP_FPS = 10
PRESENTER_FPS = 5.0
# Every page photo is shown. Past this many pictures of photos, photos are joined side by side
# (two, then three to a picture), so the frames keep their room under the 50-picture cap.
MOST_PAGE_PHOTOS = 10
# Bump when what the judge is handed changes, so its answers get a new judge id.
CONTEXT = "name+description+all photos v1"


@dataclass(frozen=True)
class Question:
    id: str
    level: str  # picture, clip, ad
    purpose: str  # the gateway purpose it is asked under

    def prompt(self) -> str:
        return (FOLDER / f"{self.id}.md").read_text(encoding="utf-8").strip()


QUESTIONS: dict[str, Question] = {
    q.id: q
    for q in (
        Question("P1", "picture", "eval_judge_picture"),
        Question("P2", "picture", "eval_judge_picture"),
        # P3 (person pasted onto the room) removed Nov 14: no model could judge it
        # (backend/evals/human-only-checks.md on main).
        Question("P4", "picture", "eval_judge_picture"),
        Question("C5", "clip", "eval_judge_clip"),
        Question("C6", "clip", "eval_judge_clip"),
        Question("C7", "clip", "eval_judge_clip"),
        Question("C8", "clip", "eval_judge_clip"),
        Question("C9", "clip", "eval_judge_clip"),
        Question("C10", "clip", "eval_judge_clip"),
        Question("A11", "ad", "eval_judge_ad"),
        # Asked of each talking clip of an ad; Julian's presenter grades are whole-ad ticks, so
        # the ad fails A12 when any of its talking clips fails.
        Question("A12", "ad", "eval_judge_presenter"),
    )
}


def shared() -> str:
    return SHARED.read_text(encoding="utf-8").strip()


def judge_id(question: Question) -> str:
    """The question's id plus a short hash of everything it is told: the shared instructions,
    its own prompt, the answer's shape and the frame rate, so a change makes a new judge."""
    from evals.judge_prototype.ask import answer_shape

    told = "\n".join(
        [
            shared(),
            question.prompt(),
            json.dumps(answer_shape(question).model_json_schema(), sort_keys=True),
            f"clip {CLIP_FPS} fps, presenter {PRESENTER_FPS} fps, photos {MOST_PAGE_PHOTOS}",
            CONTEXT,
        ]
    )
    return f"{question.id}:{hashlib.sha256(told.encode()).hexdigest()[:8]}"


def applies(question: Question, item: Any) -> bool:
    """Whether `question` is asked of `item`: its level, and for A12 an ad with talking clips,
    for A11 an ad with stills."""
    if question.level != item.level:
        return False
    if question.id == "A12":
        return bool(item.talking_clips)
    if question.id == "A11":
        return bool(item.ad_stills)
    return True


def parse(given: str | None) -> list[Question]:
    if not given:
        return list(QUESTIONS.values())
    ids = [x.strip().upper() for x in given.split(",") if x.strip()]
    unknown = [x for x in ids if x not in QUESTIONS]
    if unknown:
        raise SystemExit(f"Unknown question(s): {', '.join(unknown)}")
    return [QUESTIONS[x] for x in ids]
