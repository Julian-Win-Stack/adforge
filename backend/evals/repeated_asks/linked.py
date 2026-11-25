"""Read what happened to scenes that were meant to change together."""

from typing import TYPE_CHECKING, Any

from .cases import Case, WayOut

if TYPE_CHECKING:
    from agents.asked_changes import ChangeRecord

FIRST_SCENES = (1, 2, 3)
CHOICES: dict[WayOut, tuple[str, tuple[int, ...], tuple[int, ...]]] = {
    "shot": ("Use a different shot for scene 2", (2,), ()),
    "approach": ("Use a different approach for all three scenes", FIRST_SCENES, ()),
    "restore": ("Put the changed scenes back", (), (1, 3)),
}


def intentions(case: Case, number: int) -> dict[int, int]:
    """The requested scenes, and the ask that introduced their shared change."""
    if number == 1 or case.kind == "all_scenes_fail":
        return dict.fromkeys(FIRST_SCENES, 1)
    if case.way_out is not None and number == 5:
        return dict.fromkeys(CHOICES[case.way_out][1], 5)
    if case.kind in ("one_scene_fails", "picks_way_out"):
        return {2: 1}
    if case.kind == "scene_joins_later":
        return {4: 1} if number == 2 else {}
    return {(1, 3, 1)[number - 2]: number}


def score(session: dict[str, Any], changes: list[ChangeRecord], case: Case) -> dict[str, Any]:
    asks = session["asks"]
    if case.kind == "one_scene_fails":
        refilmed = sum(
            run["scene"] in (1, 3) for ask in asks if ask["number"] > 1 for run in _filmed(ask)
        )
        return {"refilmed_other_scenes": refilmed}
    if case.way_out is not None:
        label, films, restores = CHOICES[case.way_out]
        pick = next((ask for ask in asks if ask["number"] == 5), None)
        runs = _filmed(pick) if pick else []
        filmed = sorted(run["scene"] for run in runs)
        restored = pick.get("restored", []) if pick else []
        # The initial scripted ad takes one passing clip per scene, its v1.
        exact = (
            pick is not None
            and filmed == list(films)
            and all(run.get("passed") for run in runs)
            and [(entry["scene"], entry["version"]) for entry in restored]
            == [(scene, 1) for scene in restores]
        )
        return {
            "choice": {
                "case": case.id,
                "repeat": session["repeat"],
                "picked": label,
                "filmed": filmed,
                "restored": restored,
                "exact": exact,
            }
        }
    if case.kind == "scene_joins_later":
        return {"joined_on_first_try": _joined(asks, changes)}
    return {}


def _filmed(ask: dict[str, Any]) -> list[dict[str, Any]]:
    return [run for run in ask["runs"] if run.get("filmed")]


def _joined(asks: list[dict[str, Any]], changes: list[ChangeRecord]) -> bool:
    first = next((ask for ask in asks if ask["number"] == 1), None)
    joins = next((ask for ask in asks if ask["number"] == 2), None)
    if not first or not joins or [run["scene"] for run in _filmed(joins)] != [4]:
        return False
    for change in changes:
        initially_covered = {
            scene["scene"]
            for scene in change["scenes"]
            if any(ask["message_id"] == first["message_id"] for ask in scene["asks"])
        }
        if initially_covered >= set(FIRST_SCENES) and any(
            scene["scene"] == 4
            and any(
                ask["message_id"] == joins["message_id"]
                and any(run["try_number"] == 1 for run in ask["runs"])
                for ask in scene["asks"]
            )
            for scene in change["scenes"]
        ):
            return True
    return False


def group_scores(group: str, sessions: list[dict[str, Any]]) -> dict[str, Any]:
    records = [session["linked"] for session in sessions]
    if group == "one_scene_fails":
        return {"refilmed_other_scenes": sum(record["refilmed_other_scenes"] for record in records)}
    if group == "picks_way_out":
        return {"choices": [record["choice"] for record in records]}
    if group == "scene_joins_later":
        return {"joined_on_first_try": sum(record["joined_on_first_try"] for record in records)}
    return {}


def restored_scenes(restored: list[dict[str, int]]) -> str:
    return ", ".join(f"{row['scene']} (v{row['version']})" for row in restored) or "none"
