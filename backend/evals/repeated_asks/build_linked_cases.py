"""PROTOTYPE #738 linked scenes: writes cases_linked.json, 26 scripted sessions where one
change covers several scenes. The ads have 3 scenes, so "a scene joins later" starts on
scenes 1 and 2 and adds scene 3. Run from backend/:

    uv run python -m evals.repeated_asks.build_linked_cases
"""

import json
from pathlib import Path
from typing import Any

from .build_v2_cases import NORMAL

HERE = Path(__file__).parent
OUT = HERE / "cases_linked.json"
REASON = "The video model keeps drawing the product in the old colour in this shot."
SHOPS = ["brush", "cream", "floss", "bottle", "roller", "lens", "speaker", "vacuum"]


def _msg(say: str, look: str, by_scene: dict[int, str], default: str = "passes") -> dict[str, Any]:
    message: dict[str, Any] = {"say": say, "ending": default, "look": look}
    if by_scene:
        message["by_scene"] = {str(k): v for k, v in by_scene.items()}
    if default in ("gives_up", "breaks") or "gives_up" in by_scene.values():
        message["reason"] = REASON
    if message.get("reason") and default not in ("gives_up", "breaks"):
        # The case file allows a reason only with a give-up default; keep the give-up
        # reason on the scene endings by making the default the failing one.
        message["ending"] = "gives_up"
        message["by_scene"] = {str(n): by_scene.get(n, default) for n in (1, 2, 3)}
    return message


def build() -> dict[str, Any]:
    shops = json.loads((HERE / "cases.json").read_text())["shops"]
    cases: list[dict[str, Any]] = []

    def opener(shop: dict[str, Any]) -> str:
        return (
            f"The {shop['product']} is the wrong colour in all three scenes. It should be "
            f"{shop['colour']}, like on the page."
        )

    # 1. One scene keeps failing (8).
    for n, key in enumerate(SHOPS):
        shop = shops[key]
        bad = "gives_up" if n % 2 == 0 else "passes_missing"
        look = "Scenes 1 and 3 show the right colour; scene 2 still shows the old colour."
        two = {1: "passes", 2: bad, 3: "passes"}
        only_two = {2: bad}
        messages = [
            _msg(opener(shop), look, two),
            _msg("Scene 2 still has the wrong colour.", look, only_two, "passes"),
            _msg("Scene 2 is still the wrong colour. Please fix it.", look, only_two),
            _msg("The colour in scene 2 is still off. It must match the others.", look, only_two),
            _msg("Scene 2 still doesn't match. Try again.", look, only_two),
        ]
        cases.append(
            {
                "id": f"one-scene-fails-{n + 1:02d}-{key}",
                "kind": "one_scene_fails",
                "shop": key,
                "messages": messages,
            }
        )

    # 2. The owner picks a way out (6): two per way out.
    picks: list[tuple[str, str, dict[int, str]]] = [
        ("shot", "OK, use a different shot for scene 2 only.", {2: "passes"}),
        (
            "all",
            "Let's take a different approach for all three scenes then.",
            {1: "passes", 2: "passes", 3: "passes"},
        ),
        ("back", "Put scenes 1 and 3 back to how they were before, so everything matches.", {}),
    ]
    for n, key in enumerate(SHOPS[:6]):
        shop = shops[key]
        name, pick, endings = picks[n % 3]
        look = "Scenes 1 and 3 show the right colour; scene 2 still shows the old colour."
        failing = {1: "passes", 2: "gives_up", 3: "passes"}
        messages = [
            _msg(opener(shop), look, failing),
            _msg("Scene 2 still has the wrong colour.", look, {2: "gives_up"}),
            _msg("Scene 2 is still the wrong colour. Please fix it.", look, {2: "gives_up"}),
            _msg("The colour in scene 2 is still off.", look, {2: "gives_up"}),
            _msg(pick, "The change asked for is there.", endings),
        ]
        cases.append(
            {
                "id": f"picks-{name}-{n + 1:02d}-{key}",
                "kind": "picks_way_out",
                "shop": key,
                "messages": messages,
            }
        )

    # 3. All three scenes keep failing (4).
    for n, key in enumerate(SHOPS[2:6]):
        shop = shops[key]
        look = "All three scenes still show the old colour."
        fail = {1: "gives_up", 2: "gives_up", 3: "gives_up"}
        messages = [
            _msg(opener(shop), look, fail),
            _msg("The colour is still wrong in all three scenes.", look, fail),
            _msg("Still the wrong colour everywhere. Please fix it.", look, fail),
            _msg("All three scenes still have the wrong colour.", look, fail),
            _msg("Try the colour again in every scene.", look, fail),
        ]
        cases.append(
            {
                "id": f"all-fail-{n + 1:02d}-{key}",
                "kind": "all_scenes_fail",
                "shop": key,
                "messages": messages,
            }
        )

    # 4. A scene joins later (4): scenes 1 and 2 first, then scene 3.
    for n, key in enumerate(SHOPS[4:8]):
        shop = shops[key]
        look = "The change asked for is there."
        messages = [
            _msg(
                f"The {shop['product']} is the wrong colour in scenes 1 and 2. It should be "
                f"{shop['colour']}, like on the page.",
                look,
                {},
            ),
            _msg("Same colour problem in scene 3.", look, {}),
            _msg("Thanks, the colour is right everywhere now.", look, {}),
        ]
        cases.append(
            {
                "id": f"joins-later-{n + 1:02d}-{key}",
                "kind": "scene_joins_later",
                "shop": key,
                "messages": messages,
            }
        )

    # 5. The fix works everywhere; the owner moves on (4).
    for n, key in enumerate(SHOPS[:4]):
        shop = shops[key]
        look = "The change asked for is there."
        messages = [_msg(opener(shop), look, {})] + [_msg(text, look, {}) for _, text in NORMAL[:3]]
        cases.append(
            {
                "id": f"works-everywhere-{n + 1:02d}-{key}",
                "kind": "works_everywhere",
                "shop": key,
                "messages": messages,
            }
        )
    return {"shops": shops, "cases": cases}


if __name__ == "__main__":
    built = build()
    OUT.write_text(json.dumps(built, indent=1) + "\n")
    print(
        f"{len(built['cases'])} cases, "
        f"{sum(len(c['messages']) for c in built['cases'])} owner messages -> {OUT}"
    )
