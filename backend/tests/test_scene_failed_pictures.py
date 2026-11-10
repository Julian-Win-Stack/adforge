"""A B-roll scene never films a picture that failed its check; a talking scene still may.
A scene changed after its picture tries ran out gets 3 new ones (spec #514, ticket #518)."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel, turn
from gateway.types import Turn
from jobs.models import ProducedItem

from .conftest import HeldSteps, facts_ok, handoffs, paid_for, recorded, results_of
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_agent_changes import change
from .test_scene_end_pictures import END_PICTURE
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

pytestmark = pytest.mark.django_db(transaction=True)
NEVER = "A B-roll scene never films a failed picture: change the scene with the ladder."
FLOATS = {**PICTURE_PASSES, "physics": {"passes": False, "says": "The object floats."}}
NO_LABEL = {**PICTURE_PASSES, "product_same": {"passes": False, "says": "The label is gone."}}
# Scene 2 is B-roll and scene 3 talking in the checked mug plan.
BROLL, TALKING = 2, 3


def closest(scene: int) -> Callable[[], Turn]:
    """The scene agent filming its first failed starting picture as the closest one."""

    def call() -> Turn:
        picture = ProducedItem.objects.filter(
            kind="starting_picture", scene__number=scene
        ).earliest("version")
        return turn(
            calls=[
                (
                    "film",
                    {
                        **FILM,
                        "closest_picture": picture.pk,
                        "reason": "Its shape is the closest to the photos.",
                    },
                )
            ]
        )

    return call


def draws(count: int) -> list[Turn]:
    """`count` turns of the scene agent drawing a new starting picture, each its own call."""
    return [turn(calls=[("draw_starting_picture", PICTURE_FORM)]) for _ in range(count)]


def start(fake_model: FakeModel, say: Callable[..., None], scene: int) -> None:
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": scene, "instruction": "Show use.", "quotes": ["go on"]}),
    )


def test_a_broll_scene_is_refused_its_closest_failed_picture_and_nothing_is_paid(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.answer_unscripted("check_scene_picture", lambda _: FLOATS)
    fake_model.respond(
        "scene_agent",
        *draws(6),
        closest(BROLL),
        turn(calls=[("give_up", {"reason": "Every picture failed."})]),
    )
    start(fake_model, say, BROLL)
    run(fake_model, steps)
    assert results_of("film") == [f"Refused: {NEVER} Nothing was done."]
    told = next(r for r in fake_model.turns_taken if r.purpose == "scene_agent").instructions
    assert "A B-roll scene never films a failed starting or end picture." in " ".join(told.split())
    assert "make_broll_clip" not in paid_for()
    assert not ProducedItem.objects.filter(kind="clip").exists()


def test_a_broll_scene_is_refused_its_failed_end_picture_once_its_tries_are_used_up(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: Any,
    session_id: str,
) -> None:
    fake_model.respond("check_scene_picture", PICTURE_PASSES, *([NO_LABEL] * 6))
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        *[turn(calls=[("make_end_picture", END_PICTURE)]) for _ in range(5)],
        turn(calls=[("film", FILM)]),
        turn(calls=[("make_end_picture", END_PICTURE)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "Every end picture failed."})]),
    )
    start(fake_model, say, BROLL)
    run(fake_model, steps)
    tried, refused = results_of("film")
    # With end-picture tries left, the agent is told to make it again, as before.
    assert "make it again" in tried
    assert refused == f"Refused: {NEVER} Nothing was done."
    assert paid_for().count("make_end_picture") == 6
    assert "make_broll_clip" not in paid_for()
    assert not any(kind == "failed_end_picture_filmed" for kind, _ in _warnings(api, session_id))


def test_a_talking_scene_films_its_closest_failed_picture_with_a_developer_notice(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: Any,
    session_id: str,
) -> None:
    fake_model.answer_unscripted("check_scene_picture", lambda _: FLOATS)
    fake_model.respond(
        "scene_agent",
        *draws(6),
        closest(TALKING),
        turn(calls=[("give_up", {"reason": "This clip should have passed."})]),
    )
    start(fake_model, say, TALKING)
    run(fake_model, steps)
    assert paid_for().count("make_broll_clip") == 1, results_of("film")
    clip = ProducedItem.objects.get(kind="clip", scene__number=TALKING)
    first = ProducedItem.objects.filter(kind="starting_picture", scene__number=TALKING)
    assert clip.picture_id == first.earliest("version").pk
    assert any("closest failed picture" in text for _, text in recorded(api, session_id))
    assert any(kind == "closest_picture_filmed" for kind, _ in _warnings(api, session_id))


def test_a_talking_scene_films_its_last_failed_end_picture_with_a_developer_notice(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: Any,
    session_id: str,
) -> None:
    fake_model.respond("check_scene_picture", PICTURE_PASSES, *([NO_LABEL] * 6))
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        *[turn(calls=[("make_end_picture", END_PICTURE)]) for _ in range(6)],
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "This clip should have passed."})]),
    )
    start(fake_model, say, TALKING)
    run(fake_model, steps)
    [clip] = handoffs("make_broll_clip")
    last = ProducedItem.objects.filter(kind="end_picture").latest("version")
    assert clip["end_picture"] == last.file
    [notice] = [
        text for kind, text in _warnings(api, session_id) if kind == "failed_end_picture_filmed"
    ]
    assert f"end picture {last.pk}" in notice
    assert "all 6 end-picture tries" in notice


def test_a_broll_scene_changed_after_six_failed_pictures_gets_three_new_tries(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.answer_unscripted("check_scene_picture", lambda _: FLOATS)
    fake_model.respond("fact_check", facts_ok(BROLL))
    fake_model.respond(
        "scene_agent",
        *draws(7),
        turn(calls=[change()]),
        turn(calls=[change()]),
        *draws(4),
        turn(calls=[("give_up", {"reason": "Every picture failed."})]),
    )
    start(fake_model, say, BROLL)
    run(fake_model, steps)
    drawn = results_of("draw_starting_picture")
    assert "6 starting-picture tries" in drawn[6]
    assert "change the scene with the ladder" in drawn[6]
    changed = results_of("change_scene")[-1]
    # No end picture was made, so the end-picture budget of 6 is not used up and stays.
    assert "gets 3 new starting-picture tries." in changed
    assert "Used in this run: starting pictures 6 of 9, end pictures 0 of 6." in changed
    assert paid_for().count("draw_scene_picture") == 9
    # The tries line reads the run's raised limit, and the first failed picture's steps carry
    # the failed-picture rule.
    assert "Used in this run: starting pictures 7 of 9, end pictures 0 of 6" in drawn[7]
    assert "A B-roll scene never films a failed starting or end picture" in drawn[0]
    assert drawn[-1].startswith("Refused:")
    assert "9 starting-picture tries" in drawn[-1]


def test_a_run_never_goes_past_twelve_tries_of_each_kind_of_picture(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.answer_unscripted("check_scene_picture", lambda _: FLOATS)
    fake_model.answer_unscripted("fact_check", lambda _: facts_ok(BROLL))
    actions = [
        "Tea is stirred in the mug with a spoon.",
        "A spoon of honey drops into the mug.",
        "A tea bag is lifted out of the mug.",
    ]
    script = draws(6)
    for action in actions:
        script += [turn(calls=[change(shows=action)]) for _ in range(2)] + draws(3)
    fake_model.respond(
        "scene_agent",
        *script,
        turn(calls=[("give_up", {"reason": "Every picture failed."})]),
    )
    start(fake_model, say, BROLL)
    run(fake_model, steps)
    assert paid_for().count("draw_scene_picture") == 12
    assert "starting pictures 12 of 12" in results_of("change_scene")[-1]
    drawn = results_of("draw_starting_picture")
    assert all(text.startswith("Refused:") for text in drawn[-3:])
    assert "12 starting-picture tries" in drawn[-1]


def test_a_broll_scene_changed_only_to_talking_after_six_failed_pictures_gets_new_tries(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from jobs.models import Scene

    fake_model.answer_unscripted("check_scene_picture", lambda _: FLOATS)
    scene = Scene.objects.get(number=BROLL)
    to_talking = change(
        line=scene.line,
        shows=scene.shows,
        kind="talking",
        talking_style="talking while doing it",
        product_state="in use",
    )
    fake_model.respond(
        "scene_agent",
        *draws(6),
        turn(calls=[to_talking]),
        *draws(1),
        turn(calls=[("give_up", {"reason": "Every picture failed."})]),
    )
    start(fake_model, say, BROLL)
    run(fake_model, steps)
    changed = results_of("change_scene")[-1]
    assert "kind" in changed.split(".")[0], changed
    assert "shows" not in changed.split(".")[0]
    assert "3 new starting-picture tries" in changed
    assert paid_for().count("draw_scene_picture") == 7


def test_a_scene_changed_before_any_picture_tries_ran_out_gets_no_new_ones(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.answer_unscripted("check_scene_picture", lambda _: FLOATS)
    fake_model.respond("fact_check", facts_ok(BROLL))
    fake_model.respond(
        "scene_agent",
        *draws(2),
        turn(calls=[change()]),
        turn(calls=[change()]),
        *draws(5),
        turn(calls=[("give_up", {"reason": "Every picture failed."})]),
    )
    start(fake_model, say, BROLL)
    run(fake_model, steps)
    assert "new" not in results_of("change_scene")[-1]
    assert paid_for().count("draw_scene_picture") == 6
    assert "All 6 starting-picture tries" in results_of("draw_starting_picture")[-1]


def test_a_broll_scene_is_told_to_change_the_scene_after_its_sixth_failed_end_picture(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("check_scene_picture", PICTURE_PASSES, *([NO_LABEL] * 6))
    fake_model.respond(
        "scene_agent",
        *draws(1),
        *[turn(calls=[("make_end_picture", END_PICTURE)]) for _ in range(7)],
        turn(calls=[("give_up", {"reason": "Every end picture failed."})]),
    )
    start(fake_model, say, BROLL)
    run(fake_model, steps)
    refused = results_of("make_end_picture")[-1]
    assert refused.startswith("Refused: All 6 end-picture tries of this run have been used.")
    assert "change the scene with the ladder for new picture tries" in refused
    assert paid_for().count("make_end_picture") == 6


def _warnings(api: Any, session_id: str) -> list[tuple[str, str]]:
    return [
        (warning["kind"], warning["text"])
        for warning in api.get(f"/api/sessions/{session_id}/warnings/").json()
    ]
