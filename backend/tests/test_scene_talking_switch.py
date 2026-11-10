"""A B-roll scene switched to talking gets 2 clips of its own, even once the run's 6 clips are
used, and its run stays open for that step until then (spec #514, ticket #521)."""

from collections.abc import Callable

import pytest

from gateway.fake import FakeModel, turn
from gateway.types import Turn
from jobs.models import AgentFix, Scene

from .conftest import HeldSteps, paid_for, results_of
from .test_producer_auto_fix import FLOATS, failing
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_agent_changes import change
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

pytestmark = pytest.mark.django_db(transaction=True)
# Scene 2 is B-roll in the checked mug plan.
BROLL = 2
TALKING_STEP = "switch the scene to talking with change_scene (step 5)"


def draw() -> Turn:
    return turn(calls=[("draw_starting_picture", PICTURE_FORM)])


def film() -> Turn:
    return turn(calls=[("film", FILM)])


def give_up() -> Turn:
    return turn(calls=[("give_up", {"reason": "Steps 1 to 5 are tried."})])


def clips(count: int) -> list[Turn]:
    """`count` clips, each filmed from a new starting picture."""
    return [each for _ in range(count) for each in (draw(), film())]


def start(fake_model: FakeModel, say: Callable[..., None]) -> None:
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": BROLL, "instruction": "Show use.", "quotes": ["go on"]}),
    )


def every_clip_fails(fake_model: FakeModel) -> None:
    fake_model.answer_unscripted("check_clip_product", lambda _: failing(FLOATS))


def switch(kind: str = "talking") -> Callable[[], Turn]:
    """The scene agent switching the B-roll scene to `kind`, keeping its line and action."""

    def call() -> Turn:
        scene = Scene.objects.get(number=BROLL)
        talking = (
            {"talking_style": "talking while doing it", "product_state": "in use"}
            if kind == "talking"
            else {"kind_why": "Her hands can show the action at arm's length."}
        )
        return turn(calls=[change(line=scene.line, shows=scene.shows, kind=kind, **talking)])

    return call


def test_six_failed_broll_clips_leave_the_run_open_with_the_talking_step(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    every_clip_fails(fake_model)
    fake_model.respond("scene_agent", *clips(7), give_up())
    start(fake_model, say)
    run(fake_model, steps)

    filmed = results_of("film")
    assert paid_for().count("make_broll_clip") == 6
    # The 6th clip's result tells the agent the run stays open for the talking step.
    assert TALKING_STEP in filmed[5]
    assert all(TALKING_STEP not in each for each in filmed[:5])
    assert filmed[6].startswith("Refused:")
    assert TALKING_STEP in filmed[6]
    assert "2 clips of its own" in filmed[6]
    assert "or give up" in filmed[6]
    # The run ended by the agent's give-up, not by the cap.
    fixing = AgentFix.objects.get()
    assert not fixing.ended_by_cap
    assert fake_model.left("scene_agent") == 0


def test_a_switch_to_talking_films_two_clips_of_its_own_then_the_run_ends_by_the_cap(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    every_clip_fails(fake_model)
    fake_model.respond("scene_agent", *clips(6), switch(), *clips(2), give_up())
    start(fake_model, say)
    run(fake_model, steps)

    filmed = results_of("film")
    assert paid_for().count("make_broll_clip") == 8
    assert "clips 7 of 8." in filmed[6]
    assert "clips 8 of 8." in filmed[7]
    assert all(TALKING_STEP not in each for each in filmed[6:])
    # The 8th failed clip ends the run by the cap: the agent wasn't given its give-up turn.
    assert AgentFix.objects.get().ended_by_cap
    assert fake_model.left("scene_agent") == 1


def test_a_last_broll_render_that_fails_keeps_the_run_open_for_the_talking_step(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from gateway.types import ClipFailed

    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    every_clip_fails(fake_model)
    fake_model.respond(
        "make_clip", *[{} for _ in range(5)], ClipFailed("Creatify rejected the render")
    )
    fake_model.respond("scene_agent", *clips(7), give_up())
    start(fake_model, say)
    run(fake_model, steps)

    filmed = results_of("film")
    assert len(filmed) == 7
    assert filmed[6].startswith("Refused:")
    assert TALKING_STEP in filmed[6]
    fixing = AgentFix.objects.get()
    assert not fixing.ended_by_cap
    assert fake_model.left("scene_agent") == 0


def test_a_scene_switched_to_talking_films_its_closest_picture_past_its_broll_pictures(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from jobs.models import ProducedItem

    floats = {**PICTURE_PASSES, "physics": {"passes": False, "says": "The mug floats."}}
    # The B-roll picture passed; every picture drawn after the switch fails.
    fake_model.respond("check_scene_picture", PICTURE_PASSES)
    fake_model.answer_unscripted("check_scene_picture", lambda _: floats)
    every_clip_fails(fake_model)

    def closest() -> Turn:
        first_talking = ProducedItem.objects.filter(
            kind="starting_picture", scene__number=BROLL
        ).order_by("version")[1]
        reason = "Its shape is the closest to the photos."
        return turn(
            calls=[("film", {**FILM, "closest_picture": first_talking.pk, "reason": reason})]
        )

    fake_model.respond(
        "scene_agent", *clips(1), switch(), *[draw() for _ in range(5)], closest, give_up()
    )
    start(fake_model, say)
    run(fake_model, steps)

    filmed = results_of("film")
    assert paid_for().count("make_broll_clip") == 2, filmed[-1]
    pictures = ProducedItem.objects.filter(kind="starting_picture", scene__number=BROLL)
    talking_clip = ProducedItem.objects.filter(kind="clip", scene__number=BROLL).latest("version")
    assert talking_clip.picture_id == pictures.order_by("version")[1].pk


def test_switching_back_and_forth_adds_no_clips(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    every_clip_fails(fake_model)
    fake_model.respond(
        "scene_agent",
        *clips(6),
        switch(),
        *clips(1),
        switch("broll"),
        # Back in B-roll, the run's 2 extra clips can't be filmed: they are the talking scene's.
        *clips(1),
        switch(),
        *clips(2),
        give_up(),
    )
    start(fake_model, say)
    run(fake_model, steps)

    filmed = results_of("film")
    switched = results_of("change_scene")
    assert all(each.startswith("The scene row changed:") for each in switched), switched
    assert paid_for().count("make_broll_clip") == 8
    assert "clips 7 of 8." in filmed[6]
    assert filmed[7].startswith("Refused:")
    assert "switch back to talking" in filmed[7]
    assert "clips 8 of 8." in filmed[8]
    # The 8th failed clip ends the run by the cap; the second switch added no clips.
    assert AgentFix.objects.get().ended_by_cap
    assert len(filmed) == 9


def test_a_switch_with_no_starting_picture_tries_left_says_so(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .conftest import facts_ok

    floats = {**PICTURE_PASSES, "physics": {"passes": False, "says": "The mug floats."}}
    fake_model.answer_unscripted("check_scene_picture", lambda _: floats)
    fake_model.answer_unscripted("fact_check", lambda _: facts_ok(BROLL))
    actions = ["Tea is stirred in the mug with a spoon.", "A spoon of honey drops into the mug."]
    script = [draw() for _ in range(6)]
    for action in actions:
        script += [turn(calls=[change(shows=action)]) for _ in range(2)]
        script += [draw() for _ in range(3)]
    fake_model.respond("scene_agent", *script, switch(), give_up())
    start(fake_model, say)
    run(fake_model, steps)

    assert paid_for().count("draw_scene_picture") == 12
    switched = results_of("change_scene")[-1]
    assert switched.startswith("The scene row changed:"), switched
    assert "no starting-picture tries left" in switched
