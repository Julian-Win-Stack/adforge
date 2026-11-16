"""The eval questions (spec #644): which items each one is asked of, on which model, what it
is shown, and its prompt, read from `questions/<id>.md`. Every call of one item shares the same
instructions (`questions/_judge.md`); the questions shown the same pictures share those too, and
only the question, sent last, differs."""

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
    name: str  # what it checks, in plain words, for the report
    # Clip questions only: which frames (the clip judge's picture sets: "sides" = CLIP_FPS a
    # second, "frames" = 5 a second) and whether the page photos are shown beside them.
    frames: str = "sides"
    photos: bool = True

    @property
    def inputs(self) -> tuple[str, bool]:
        """What the question is shown; questions with the same inputs share one cut."""
        return (self.frames, self.photos)

    def prompt(self) -> str:
        return (FOLDER / f"{self.id}.md").read_text(encoding="utf-8").strip()


QUESTIONS: dict[str, Question] = {
    q.id: q
    for q in (
        Question("P1", "picture", "eval_judge_picture", "picture: real product"),
        # P2 (nothing impossible in the picture) removed Nov 16: none of Julian's fails were of
        # that kind, and the clip questions catch the same faults one step later.
        # P3 (person pasted onto the room) removed Nov 14: no model could judge it
        # (backend/evals/human-only-checks.md on main).
        Question("P4", "picture", "eval_judge_picture", "picture: staged like real life"),
        Question("C5", "clip", "eval_judge_clip", "clip: real product, same size"),
        Question("C6", "clip", "eval_judge_clip", "clip: nothing impossible"),
        Question("C7", "clip", "eval_judge_clip", "clip: used the right way"),
        Question("C8", "clip", "eval_judge_clip", "clip: cream or liquid real"),
        Question("C9", "clip", "eval_judge_clip", "clip: camera moves naturally"),
        Question("C10", "clip", "eval_judge_clip", "clip: staged like real life"),
        # Added Nov 16 as tested on regression-3 (eval-store/runs/regression-3/judge-deep-dive,
        # e3): every frame at 5 a second, no page photos.
        Question("C13", "clip", "eval_judge_clip", "clip: hand", frames="frames", photos=False),
        Question(
            "C14", "clip", "eval_judge_clip", "clip: action works", frames="frames", photos=False
        ),
        Question("A11", "ad", "eval_judge_ad", "ad: same product every scene"),
        # Asked of each talking clip of an ad; Julian's presenter grades are whole-ad ticks, so
        # the ad fails A12 when any of its talking clips fails. Out of the spec since Nov 13.
        Question("A12", "ad", "eval_judge_presenter", "ad: presenter"),
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
            # Only questions shown something else carry this, so the others keep their ids.
            *([f"inputs {question.inputs}"] if question.inputs != ("sides", True) else []),
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
    if not given:  # the spec's 11: every question but the presenter one
        return [q for q in QUESTIONS.values() if q.id != "A12"]
    ids = [x.strip().upper() for x in given.split(",") if x.strip()]
    unknown = [x for x in ids if x not in QUESTIONS]
    if unknown:
        raise SystemExit(f"Unknown question(s): {', '.join(unknown)}")
    return [QUESTIONS[x] for x in ids]
