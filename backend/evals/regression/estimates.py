"""Catalogue prices and recent actual costs for the selected regression stages."""

from collections.abc import Sequence
from decimal import Decimal

from .scenarios import Scenario
from .store import Store, read_json


def _planning_cost(*, page: bool = True) -> Decimal:
    from gateway import catalog

    calls = {
        "produce": (6, 12_000, 1_500),
        "plan_ad": (1, 30_000, 4_000),
        "fact_check": (1, 18_000, 2_000),
        "pick_photos": (1, 15_000, 2_000),
        "pick_side_photos": (1, 15_000, 2_000),
        "label_photo": (10, 5_000, 500),
        "copy_page_text": (1, 30_000, 10_000),
        "check_page": (1, 30_000, 1_000),
    }
    if not page:
        for purpose in (
            "copy_page_text",
            "check_page",
            "label_photo",
            "pick_photos",
            "pick_side_photos",
        ):
            calls.pop(purpose)
    return sum(
        (
            count * catalog.cost_usd(catalog.MODEL_FOR_PURPOSE[purpose], inputs, outputs)
            for purpose, (count, inputs, outputs) in calls.items()
        ),
        Decimal(0),
    )


def _picture_cost(*, shared: bool = True) -> Decimal:
    from gateway import catalog

    image = catalog.picture_cost_usd(
        catalog.MODEL_FOR_PURPOSE["draw_scene_picture"], 4_000, 3_000, 2_000
    )
    # Portrait, shared person/room, three scene pictures and one ordinary redraw.
    return (
        (7 if shared else 4) * image
        + 6 * catalog.cost_usd(catalog.MODEL_FOR_PURPOSE["scene_agent"], 10_000, 1_000)
        + 3 * catalog.cost_usd(catalog.MODEL_FOR_PURPOSE["check_scene_picture"], 10_000, 1_500)
    )


def _clip_cost(*, music: bool = True) -> Decimal:
    from gateway import catalog

    # Three clips plus normal retries, and the scene/producer turns/checks they need.
    return (
        catalog.video_cost_usd(catalog.MODEL_FOR_PURPOSE["make_broll_clip"], 55)
        + 15 * catalog.cost_usd(catalog.MODEL_FOR_PURPOSE["scene_agent"], 50_000, 3_500)
        + 4 * catalog.cost_usd(catalog.MODEL_FOR_PURPOSE["produce"], 40_000, 2_000)
        + sum(
            catalog.cost_usd(catalog.MODEL_FOR_PURPOSE[purpose], 20_000, 5_000) * count
            for purpose, count in (
                ("check_clip_motion", 4),
                ("check_clip_product", 2),
                ("check_clip_references", 2),
            )
        )
        + 2
        * catalog.picture_cost_usd(
            catalog.MODEL_FOR_PURPOSE["draw_scene_picture"], 4_000, 3_000, 2_000
        )
        + (catalog.music_cost_usd(catalog.MODEL_FOR_PURPOSE["make_music"], 35) if music else 0)
        + catalog.transcription_cost_usd(catalog.MODEL_FOR_PURPOSE["transcribe_line"], 55)
    )


def _note_cost() -> Decimal:
    from gateway import catalog

    return (
        catalog.video_cost_usd(catalog.MODEL_FOR_PURPOSE["make_broll_clip"], 12)
        + catalog.picture_cost_usd(
            catalog.MODEL_FOR_PURPOSE["draw_scene_picture"], 4_000, 3_000, 2_000
        )
        + 2 * catalog.cost_usd(catalog.MODEL_FOR_PURPOSE["scene_agent"], 50_000, 3_000)
        + 4 * catalog.cost_usd(catalog.MODEL_FOR_PURPOSE["produce"], 10_000, 2_000)
        + catalog.cost_usd(catalog.MODEL_FOR_PURPOSE["check_clip_motion"], 20_000, 5_000)
    )


def estimate(
    store: Store, scenarios: Sequence[Scenario], *, to: str = "plans", from_stage: str | None = None
) -> Decimal:
    """Use actual same-stage scenario costs where known, else catalogue × typical calls."""
    planning = (
        _planning_cost(page=from_stage is None) if from_stage in (None, "plan") else Decimal(0)
    )
    pictures = (
        _picture_cost(shared=from_stage in (None, "plan"))
        if to != "plans" and from_stage not in ("clips", "notes")
        else Decimal(0)
    )
    clips = (
        _clip_cost(music=from_stage in (None, "plan"))
        if to == "full" and from_stage != "notes"
        else Decimal(0)
    )
    note = _note_cost() if to == "full" else Decimal(0)
    known: dict[str, Decimal] = {}
    previous = [read_json(path) for path in (store.root / "runs").glob("*/run.json")]
    for record in sorted(previous, key=lambda row: row.get("started_at", ""), reverse=True):
        if record.get("to") != to or record.get("from") != from_stage:
            continue
        for scenario in scenarios:
            if scenario.key in known or not store.scenario_done(record["name"], scenario.key):
                continue
            actual = store.read_answers(record["name"], scenario.key).get("cost_usd", 0)
            if actual > 0:
                known[scenario.key] = Decimal(str(actual))
    return sum(
        (
            known.get(
                scenario.key,
                planning
                + pictures
                + (
                    clips + note * len(scenario.owner_notes)
                    if scenario.kind == "full"
                    else Decimal(0)
                ),
            )
            for scenario in scenarios
        ),
        Decimal(0),
    )


def estimated_video_seconds(
    store: Store, scenarios: Sequence[Scenario], *, to: str, from_stage: str | None = None
) -> float:
    if to != "full":
        return 0
    known: dict[str, float] = {}
    previous = [read_json(path) for path in (store.root / "runs").glob("*/run.json")]
    for record in sorted(previous, key=lambda row: row.get("started_at", ""), reverse=True):
        if record.get("to") != "full" or record.get("from") != from_stage:
            continue
        for scenario in scenarios:
            if scenario.key in known or not store.scenario_done(record["name"], scenario.key):
                continue
            seconds = record.get("results", {}).get(scenario.key, {}).get("video_seconds", 0)
            if seconds > 0:
                known[scenario.key] = float(seconds)
    return sum(
        known.get(
            scenario.key, (0 if from_stage == "notes" else 55) + 12 * len(scenario.owner_notes)
        )
        for scenario in scenarios
        if scenario.kind == "full"
    )
