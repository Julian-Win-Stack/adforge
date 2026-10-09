"""The code checks of the rule evals (decisions/rule-evals-plan.md): exact text, no AI, run on a
saved run of the app. A run is a folder with one folder per ad ("12-gripmunk-case"), each with
one folder per scene ("scene-2", or "scene-1-talking" for a talking scene) holding the
inputs.txt the run wrote, and shop-answers.log, the shop owner's answers to the app's
questions, one per line as "<ad id>: Q '<question>' -> A '<answer>'".

Each check names the rule it keeps: A for a rule Julian's grades proved (it must stay green),
C for a fix not built yet (it starts red on round 2)."""

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Result:
    ad: str
    scene: int
    check: str
    passed: bool
    why: str = ""


@dataclass(frozen=True)
class SavedScene:
    """One B-roll scene of a saved run, read from its inputs.txt."""

    line: str
    shows: str
    kind: str
    usage: str
    result: str
    photo_jobs: list[str]
    picture_prompt: str
    motion_prompt: str
    video_prompt: str
    settings: str

    @classmethod
    def read(cls, text: str) -> SavedScene:
        def field(name: str) -> str:
            found = re.search(rf"^{re.escape(name)}: ?(.*)$", text, re.MULTILINE)
            return found.group(1).strip() if found else ""

        def block(header: str) -> str:
            """The lines after `header` up to the first empty or KEY: line."""
            found = re.search(
                rf"^{re.escape(header)}\n((?:(?![A-Z][A-Z ()-]+:).+\n?)*)", text, re.M
            )
            return found.group(1).strip() if found else ""

        sent = field("PICTURES SENT")
        return cls(
            line=field("LINE"),
            shows=field("SHOWS"),
            kind=field("BROLL_KIND"),
            usage=field("USAGE"),
            result=field("RESULT"),
            photo_jobs=[picture["job"] for picture in json.loads(sent)] if sent else [],
            picture_prompt=block("START-PICTURE PROMPT (exact):"),
            motion_prompt=field("MOTION PROMPT (planned)"),
            video_prompt=block("VIDEO PROMPT SENT (exact):"),
            settings=field("SETTINGS"),
        )


def _found(pattern: str, text: str) -> str | None:
    found = re.search(pattern, text, re.IGNORECASE)
    return found.group(0) if found else None


# A1: "real-time speed" was in the opener that passed #12 and #8 and is in today's.
def _real_time_speed(scene: SavedScene) -> str | None:
    return None if "real-time speed" in scene.video_prompt else 'no "real-time speed"'


# A4: no seconds or timings, nothing that slows it down. The opener's clip length is code's.
_TIMING = r"\b\d+(\.\d+)?\s*-?\s*(s|secs?|seconds?)\b|\bslow(ly|-motion| motion)\b|\bgently\b"


def _no_timings(scene: SavedScene) -> str | None:
    return _found(_TIMING, scene.motion_prompt)


# A6: a photo the picture model is sent shows only how something looks, never an action or a
# pose: the model copies it whatever its job says (Oct 7 #12 drop, Julian 03:58).
_ACTION = (
    r"\b(drop|fall|pour|scrub|wipe|spray|squeez|apply|appli|twist|slid|throw|toss|press|"
    r"pose|impact|bounc|splash|brush|rub|spread|swip|tap)\w*"
)
_LOOKS_ONLY_JOBS = {"the product, only how it looks", "the presenter"}


def _photo_jobs_looks_only(scene: SavedScene) -> str | None:
    for job in scene.photo_jobs:
        if job not in _LOOKS_ONLY_JOBS and (word := _found(_ACTION, job)):
            return f"photo job {job!r} names {word!r}"
    return None


# A7: the video model gets the drawn start picture, never shop photos (#12 V1 FAIL, V2 GOOD).
def _drawn_start_picture(scene: SavedScene) -> str | None:
    if not re.search(r"starting_picture=\S", scene.settings):
        return "no starting picture"
    if "example_pictures=[]" not in scene.settings:
        return "shop photos sent to the video model"
    return None


# A12: never ask for several shots (#12 3-shot FAIL). "One continuous shot" isn't required.
_SHOTS = r"\bcuts? to\b|\bshot \d|\b(second|next|another|new|third) shot\b|\bsplit[- ]screen\b"


def _one_shot(scene: SavedScene) -> str | None:
    return _found(_SHOTS, scene.video_prompt)


# A14: a hand or the product moves, never the camera (round 1 #8 s2, batch-13 #15 FAIL).
_CAMERA_MOVE = (
    r"\bzoom\w*|\bpush\w* (the camera )?(in|forward|toward)\w*|\bpull\w* (back|out)\b|"
    r"\bdoll(y|ies)\b|\borbit\w*|\bcamera (pans|moves|tilts|tracks|circles|follows|rises|"
    r"lowers|pushes|pulls)\b|\b(pans|panning) (across|to|over|left|right|up|down)\b"
)


def _camera_still(scene: SavedScene) -> str | None:
    return _found(_CAMERA_MOVE, scene.shows) or _found(_CAMERA_MOVE, scene.motion_prompt)


# C1, code side: a showcase whose one action only handles the product sells nothing (Julian
# 02:15: round 2 #12 s2, #8 s4, N3). Only the main clause counts: in round 1's #8 s4 a brush
# scrubs "while the cleaner bottle stands" beside it. A job done by handling it, such as a
# phone lowered into a bag to show it fits (round 1 #5, OK), is not filler, nor is showing the
# problem with the product beside it.
_HANDLING = (
    r"\b(holds?|holding|places?|placing|sets?|setting|rests?|resting|stands?|standing|puts?|"
    r"lowers?|lifts?|raises?|carries|carrying|picks? up|sits?)\b"
)


def _more_than_handling(scene: SavedScene) -> str | None:
    if scene.kind != "showcase":
        return None
    main = re.split(r"\b(?:while|as|with|beside|next to|nearby)\b|,", scene.shows)[0]
    return _found(_HANDLING, main)


# C3, code side: a number the scene states for what it shows is in its prompts (round 2 #12
# s5: dropped "really close to the floor"). Whether the page's number reached the scene at all
# is the AI-read side.
_NUMBER_WORDS = {
    word: str(value)
    for value, word in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve".split()
    )
} | {"twenty": "20", "thirty": "30", "fifty": "50", "hundred": "100"}
_UNIT = (
    r"(feet|foot|ft|inch(es)?|in\.|cm|mm|meters?|metres?|m|lbs?|pounds?|kg|times|drops|"
    r"degrees|°|%)"
)
_MEASURE = rf"\b(\d+(\.\d+)?|{'|'.join(_NUMBER_WORDS)})[\s-]*{_UNIT}(?!\w)"


def _numbers(text: str) -> set[str]:
    return {
        _NUMBER_WORDS.get(found.group(1).lower(), found.group(1))
        for found in re.finditer(_MEASURE, text, re.IGNORECASE)
    }


def _numbers_carried(scene: SavedScene) -> str | None:
    stated = _numbers(" ".join([scene.line, scene.shows, scene.usage, scene.result]))
    missing = stated - _numbers(f"{scene.picture_prompt} {scene.video_prompt}")
    return f"left out: {', '.join(sorted(missing))}" if missing else None


SCENE_CHECKS = {
    "A1 real-time speed": _real_time_speed,
    "A4 no timings": _no_timings,
    "A6 photo jobs are looks only": _photo_jobs_looks_only,
    "A7 drawn start picture, no shop photos": _drawn_start_picture,
    "A12 never several shots": _one_shot,
    "A14 the camera never moves": _camera_still,
    "C1 the B-roll does more than handle the product": _more_than_handling,
    "C3 a number in the scene is in its prompts": _numbers_carried,
}

FIRST_TALKING = "A16 the first scene is talking"
NO_FILLER = "C4 no B-roll filler after the owner has no photo"

# C4: the app asked for a before-and-after (or result) photo and the owner had none.
_ASKED_FOR_PROOF = r"before[- ]and[- ]after|before/after|after photo|result photo"
_NONE_GIVEN = r"\bdon'?t have\b|\bno\b|\bwithout\b|\bcan'?t\b|\bnone\b"


def _owner_had_no_proof_photo(answers: str, ad_id: str) -> bool:
    for found in re.finditer(rf"^{re.escape(ad_id)}: Q (.*) -> A (.*)$", answers, re.MULTILINE):
        question, answer = found.groups()
        if re.search(_ASKED_FOR_PROOF, question, re.I) and re.search(_NONE_GIVEN, answer, re.I):
            return True
    return False


def _scene_number(folder: Path) -> int:
    return int(folder.name.split("-")[1])


def check_ad(folder: Path, answers: str) -> list[Result]:
    """Every code check on one saved ad."""
    ad = folder.name
    scenes = sorted(
        (path for path in folder.iterdir() if path.is_dir() and path.name.startswith("scene-")),
        key=_scene_number,
    )
    results = []
    first = scenes[0]
    results.append(Result(ad, 1, FIRST_TALKING, first.name.endswith("-talking"), "opens on B-roll"))
    no_proof = _owner_had_no_proof_photo(answers, ad.split("-")[0])
    for path in scenes:
        number = _scene_number(path)
        if path.name.endswith("-talking") or not (path / "inputs.txt").exists():
            continue
        scene = SavedScene.read((path / "inputs.txt").read_text())
        for name, check in SCENE_CHECKS.items():
            why = check(scene)
            results.append(Result(ad, number, name, why is None, why or ""))
        if no_proof and path != scenes[0] and path != scenes[-1]:
            results.append(Result(ad, number, NO_FILLER, False, "B-roll after no photo"))
    return results


def check_run(run: Path) -> list[Result]:
    """Every code check on every ad of a saved run."""
    log = run / "shop-answers.log"
    answers = log.read_text() if log.exists() else ""
    return [
        result
        for folder in sorted(run.iterdir())
        if folder.is_dir() and any(folder.glob("scene-*"))
        for result in check_ad(folder, answers)
    ]
