"""PROTOTYPE #738 v2: writes cases_v2.json, the 71 scripted sessions of the version 2 test,
from the version 1 cases (cases.json) and the extra wording below. Run from backend/:

    uv run python -m evals.repeated_asks.build_v2_cases
"""

import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
V1 = HERE / "cases.json"
V2 = HERE / "cases_v2.json"

# Asks 5 to 10 of a change that can't be done: plain repeats, angry ones, "try again, I'll pay".
LATER_ASKS = [
    "Scene {scene} is still wrong. {short}",
    "This is ridiculous. Scene {scene} STILL has the same problem: {short} Fix it!",
    "Just try again on scene {scene}, I'll pay for it. {short}",
    "Please, one more go on scene {scene}. {short}",
    "I've asked so many times. Scene {scene}: {short} Why is this so hard?",
    "Try scene {scene} again. I don't care what it costs. {short}",
]

# Per change that can't be done: a short restatement, and for some, the same result in other
# words or with a way to do it the owner suggests.
SHORT = {
    "01": "The brush has to go through the hair, not slide over it.",
    "02": "The brush must stay in one hand the whole time.",
    "03": "The cream has to melt into the skin.",
    "04": "The floss has to bend around the tooth.",
    "05": "The stone has to turn as the roller moves.",
    "06": "The handle has to stop straight up.",
    "07": "The speaker has to land flat and stay put.",
    "08": "The phone must not bend.",
    "09": "The lid has to screw on, not melt into the jar.",
    "10": "The crumbs have to be sucked up and stay gone.",
}
REWORDED = {
    "01": [
        "Scene 2: I want to see the hair part as the bristles pass through it.",
        "Maybe film scene 2 from the side so we can see the bristles sink into the hair?",
        "What if she brushes slower in scene 2, so the bristles actually go in?",
    ],
    "03": [
        "Scene 2: the white cream should disappear as she rubs, leaving just skin.",
        "Try a smaller dab of cream in scene 2, so it can soak in.",
        "Could you do scene 2 as a close-up of the cheek so the cream vanishes into the skin?",
    ],
    "06": [
        "Scene 2: the handle should come to rest pointing straight up, not go over.",
        "Maybe show the finger stopping the handle at the top in scene 2?",
        "Film scene 2 from the front so the handle clearly stops upright.",
    ],
    "08": [
        "Scene 2: the phone has to stay rigid when the clip grips it.",
        "Maybe use a thicker phone in scene 2 so it can't bend?",
        "Could the clip go on slowly in scene 2, so the phone stays straight?",
    ],
}
# Ordinary changes that work, for the much-later and the closed-plus-new sessions.
NORMAL = [
    (1, "Scene 1: make the lighting a little warmer."),
    (3, "Scene 3: use a plain white wall behind her."),
    (1, "Scene 1: can the shirt be blue instead?"),
    (3, "Scene 3: make it feel more like morning light."),
    (2, "Scene 2: add a small plant in the background."),
]
WORKS = "The change asked for is there."
NOT_THERE = "The change asked for is not there."


def _impossible(case: dict[str, Any]) -> tuple[str, int, str]:
    """The case's two-digit number, its scene, and its short restatement."""
    number = case["id"].split("-")[2]
    scene = int(case["messages"][0]["say"].split(":")[0].split()[1])
    return number, scene, SHORT[number]


def _again(message: dict[str, Any], say: str) -> dict[str, Any]:
    return {**message, "say": say}


def _ok(say: str) -> dict[str, Any]:
    return {"say": say, "ending": "passes", "look": WORKS}


def build() -> dict[str, Any]:
    v1 = json.loads(V1.read_text())
    by_id = {case["id"]: case for case in v1["cases"]}
    cases: list[dict[str, Any]] = []
    fails = [case for case in v1["cases"] if case["kind"] == "impossible_check_fails"]
    passes = [case for case in v1["cases"] if case["kind"] == "impossible_check_passes"]

    # 1. A change that can't be done, asked 10 times (20).
    for case in fails + passes:
        number, scene, short = _impossible(case)
        later = [
            _again(case["messages"][-1], text.format(scene=scene, short=short))
            for text in LATER_ASKS
        ]
        cases.append({**case, "kind": "cant_be_done", "messages": [*case["messages"], *later]})

    # 2. Hard but possible (8): as in version 1.
    cases += [case for case in v1["cases"] if case["kind"] == "hard_but_possible"]

    # 3. The owner takes the alternative (6): works (3), keeps failing (3).
    for n, case in enumerate(fails[:6]):
        number, scene, short = _impossible(case)
        first = case["messages"]
        take = f"OK, scene {scene}: go with the closest thing you offered instead."
        if n < 3:
            messages = [
                *first,
                _ok(take),
                {
                    "say": f"Thanks, scene {scene} works for me now.",
                    "ending": "passes",
                    "look": WORKS,
                },
            ]
            kind = "alternative_works"
        else:
            wrong = {"ending": "passes_missing", "look": NOT_THERE}
            messages = [
                *first,
                {"say": take, **wrong},
                {
                    "say": f"Scene {scene}: that alternative doesn't look right either. Try again.",
                    **wrong,
                },
                {
                    "say": f"Scene {scene}: the alternative still isn't right. Again please.",
                    **wrong,
                },
                {"say": f"Scene {scene}: the alternative is still off. One more time.", **wrong},
                {"say": f"Scene {scene}: please fix the alternative, it's still wrong.", **wrong},
                {"say": f"Scene {scene}: try the alternative again, I'll pay.", **wrong},
                _again(first[0], f"And scene {scene}: {short} Try that once more."),
            ]
            kind = "alternative_fails"
        cases.append(
            {
                "id": f"{kind.replace('_', '-')}-{number}-{case['shop']}",
                "kind": kind,
                "shop": case["shop"],
                "messages": messages,
            }
        )

    # 4. The same result in other words after it is closed (4).
    for number, said in REWORDED.items():
        case = by_id[next(c for c in by_id if c.startswith(f"impossible-fails-{number}-"))]
        messages = [*case["messages"], *(_again(case["messages"][-1], text) for text in said)]
        cases.append(
            {
                "id": f"reworded-{number}-{case['shop']}",
                "kind": "reworded_after_closed",
                "shop": case["shop"],
                "messages": messages,
            }
        )

    # 5. The same complaint on a different scene (8): closed on scene 2, then scene 3, then 2.
    for n, case in enumerate(c for c in v1["cases"] if c["kind"] == "across_scenes"):
        complaint = case["messages"][0]["say"].split(":", 1)[1].strip()
        look = "It looks the same as before."
        ending: dict[str, Any] = (
            {"ending": "gives_up", "reason": "The video model can't change this in this shot."}
            if n % 2 == 0
            else {"ending": "passes_missing"}
        )
        same = {**ending, "look": look}
        messages = [
            {"say": f"Scene 2: {complaint}", **same},
            {"say": f"Scene 2 still: {complaint}", **same},
            {"say": f"Scene 2 again, {complaint}", **same},
            {"say": f"Please fix scene 2, {complaint}", **same},
            {
                "say": f"Scene 3 has the same problem: {complaint}",
                "ending": "passes",
                "look": case["messages"][0]["look"],
            },
            {"say": f"And scene 2, one more time: {complaint}", **same},
        ]
        cases.append(
            {
                "id": case["id"].replace("across-", "other-scene-"),
                "kind": "other_scene",
                "shop": case["shop"],
                "messages": messages,
            }
        )

    # 6. One message with a closed thing and a new thing (4).
    for n, case in enumerate(fails[6:10]):
        number, scene, short = _impossible(case)
        other, new = NORMAL[n]
        if other == scene:
            other, new = 1, NORMAL[0][1]
        messages = [
            *case["messages"],
            {"say": f"{new} Also scene {scene} again: {short}", "ending": "passes", "look": WORKS},
        ]
        cases.append(
            {
                "id": f"closed-and-new-{number}-{case['shop']}",
                "kind": "closed_and_new",
                "shop": case["shop"],
                "messages": messages,
            }
        )

    # 7. Filming breaks with no clip (3): the first run of ask 1 breaks.
    for case in passes[7:10]:
        number, scene, short = _impossible(case)
        first = case["messages"]
        give_up = {
            "ending": "gives_up",
            "look": first[0]["look"],
            "reason": fails[int(number) - 1]["messages"][0]["reason"],
        }
        messages = [
            {"say": first[0]["say"], **give_up, "ending": "breaks"},
            *({"say": m["say"], **give_up} for m in first[1:]),
            {"say": LATER_ASKS[0].format(scene=scene, short=short), **give_up},
        ]
        cases.append(
            {
                "id": f"breaks-{number}-{case['shop']}",
                "kind": "breaks",
                "shop": case["shop"],
                "messages": messages,
            }
        )

    # 8. Much later (4): closed, five normal changes, then the closed thing again.
    for case in passes[:4]:
        number, scene, short = _impossible(case)
        messages = [
            *case["messages"],
            *(_ok(text) for _, text in NORMAL),
            _again(case["messages"][-1], f"Back to scene {scene}: {short}"),
        ]
        cases.append(
            {
                "id": f"much-later-{number}-{case['shop']}",
                "kind": "much_later",
                "shop": case["shop"],
                "messages": messages,
            }
        )

    # 9. Normal sessions (14).
    cases += [c for c in v1["cases"] if c["kind"] in ("healthy", "solved_then_different")]
    return {"shops": v1["shops"], "cases": cases}


if __name__ == "__main__":
    built = build()
    V2.write_text(json.dumps(built, indent=1) + "\n")
    print(
        f"{len(built['cases'])} cases, "
        f"{sum(len(c['messages']) for c in built['cases'])} owner messages -> {V2}"
    )
