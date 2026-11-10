"""The scene agent is told the try-another-way ladder and the tries its run has used (#517),
through the chat, faking only outside services."""

from collections.abc import Callable

import pytest

from gateway.fake import FakeModel, turn

from .conftest import HeldSteps, paid_for, results_of
from .test_producer_auto_fix import FLOATS, failing
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_end_pictures import END_PICTURE
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES, picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def test_the_instructions_hold_the_ladder_and_the_starting_point_rule_but_no_photo_ask() -> None:
    from agents.scene_agent.instructions import INSTRUCTIONS
    from agents.scene_agent.lifecycle import LADDER_STEPS

    said = " ".join(INSTRUCTIONS.split())
    assert "The producer's instruction is a starting point." in said
    assert "Only the owner's checked, exact words and the plan's rules are locked." in said
    assert "change the action yourself with change_scene and say why" in said
    assert "only when the owner's own words require" in said
    assert "change it only once a picture or clip of it has failed or code refuses its move" in (
        said
    )
    assert "Call give_up only once these steps are done" in said
    assert "names the steps you tried" in said
    # The six steps, in order, each said whole.
    assert len(LADDER_STEPS) == 6
    for number, step in enumerate(LADDER_STEPS, start=1):
        assert step.startswith(f"{number}. ")
        assert " ".join(step.split()) in said
    for words in (
        "hide it right away",
        "nearest version",
        "simpler action",
        "result instead of the process",
        "talking scene",
        "keeps its last try",
    ):
        assert any(words in step for step in LADDER_STEPS), words
    # Asking the owner for a photo or video is not a step.
    ladder = " ".join(LADDER_STEPS).lower()
    assert "ask" not in ladder
    assert "photo" not in ladder
    assert "video" not in ladder


START = ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]})
DRAW = ("draw_starting_picture", PICTURE_FORM)
END = ("make_end_picture", END_PICTURE)
FILM_IT = ("film", FILM)
GIVE_UP = turn(calls=[("give_up", {"reason": "Every step is tried."})])
FAILED_PICTURE = {**PICTURE_PASSES, "physics": {"passes": False, "says": "The mug floats."}}


def picture_steps_in(result: str) -> bool:
    from agents.scene_agent.lifecycle import LADDER_STEPS

    told = [step in result for step in LADDER_STEPS]
    assert told[2:] == [False] * 4, "a picture result never carries the clip steps"
    assert told[0] == told[1]
    return told[0]


def test_the_picture_steps_come_once_at_the_runs_first_failed_picture(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "check_scene_picture",
        PICTURE_PASSES,
        FAILED_PICTURE,
        FAILED_PICTURE,
        PICTURE_PASSES,
        FAILED_PICTURE,
        PICTURE_PASSES,
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[DRAW]),
        turn(calls=[DRAW]),
        turn(calls=[DRAW]),
        turn(calls=[DRAW]),
        turn(calls=[END]),
        turn(calls=[END]),
        GIVE_UP,
    )
    calling(fake_model, say, START)
    run(fake_model, steps)

    drawn = results_of("draw_starting_picture")
    ended = results_of("make_end_picture")
    assert [picture_steps_in(each) for each in drawn] == [False, True, False, False]
    assert [picture_steps_in(each) for each in ended] == [False, False]


def test_a_failed_end_picture_first_in_the_run_gets_the_picture_steps(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "check_scene_picture", PICTURE_PASSES, FAILED_PICTURE, FAILED_PICTURE, FAILED_PICTURE
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[DRAW]),
        turn(calls=[END]),
        turn(calls=[END]),
        turn(calls=[DRAW]),
        GIVE_UP,
    )
    calling(fake_model, say, START)
    run(fake_model, steps)

    assert [picture_steps_in(each) for each in results_of("make_end_picture")] == [True, False]
    assert [picture_steps_in(each) for each in results_of("draw_starting_picture")] == [
        False,
        False,
    ]


def clip_steps_in(result: str) -> bool:
    from agents.scene_agent.lifecycle import LADDER_STEPS

    told = [step in result for step in LADDER_STEPS]
    assert told[:2] == [False, False], "a film result never carries the picture steps"
    assert told[5] is False
    assert len(set(told[2:5])) == 1
    assert ("Fix it by where the clip first goes wrong." in result) == told[2]
    return told[2]


def test_the_clip_steps_come_once_a_run_at_its_first_judge_failed_clip(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .conftest import CLIP_PRODUCT_OK

    # A word-only fail is not the judge's: the clip steps wait for the judge's first fail.
    fake_model.respond(
        "check_clip_product", CLIP_PRODUCT_OK, failing(FLOATS), failing(FLOATS), failing(FLOATS)
    )
    fake_model.respond("transcribe_line", {"text": "This is a completely different claim."})
    fake_model.respond("check_words", {"verdict": "fail", "reason": "The claim changed."})
    fake_model.respond(
        "scene_agent",
        turn(calls=[DRAW]),
        turn(calls=[FILM_IT]),
        turn(calls=[FILM_IT]),
        turn(calls=[DRAW]),
        turn(calls=[FILM_IT]),
        turn(calls=[DRAW]),
        turn(calls=[FILM_IT]),
        turn(calls=[("give_up", {"reason": "Steps 3 to 5 are tried."})]),
    )
    calling(fake_model, say, START)
    run(fake_model, steps)
    # A note opens a new run, which is told the clip steps again at its own first fail.
    fake_model.respond("check_clip_product", failing(FLOATS), failing(FLOATS))
    fake_model.respond(
        "scene_agent",
        turn(calls=[DRAW]),
        turn(calls=[FILM_IT]),
        turn(calls=[DRAW]),
        turn(calls=[FILM_IT]),
        GIVE_UP,
    )
    calling(
        fake_model,
        say,
        (
            "pass_note",
            {"scenes": [{"scene": 2, "instruction": "Try the pour again.", "quotes": ["go on"]}]},
        ),
    )
    run(fake_model, steps)

    filmed = results_of("film")
    assert all("It fails." in each for each in filmed)
    assert [clip_steps_in(each) for each in filmed] == [False, True, False, False, True, False]
    assert not any("Consider a different action" in each for each in filmed)


def test_every_picture_and_film_result_says_the_tries_the_run_has_used(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "check_scene_picture",
        FAILED_PICTURE,
        PICTURE_PASSES,
        FAILED_PICTURE,
        PICTURE_PASSES,
        PICTURE_PASSES,
    )
    fake_model.respond("check_clip_product", failing(FLOATS))
    edit = ("edit_starting_picture", {**END_PICTURE, "picks": PICTURE_FORM["picks"]})
    fake_model.respond(
        "scene_agent",
        turn(calls=[DRAW]),
        turn(calls=[edit]),
        turn(calls=[END]),
        turn(calls=[END]),
        turn(calls=[FILM_IT]),
        turn(calls=[DRAW]),
        GIVE_UP,
    )
    calling(fake_model, say, START)
    run(fake_model, steps)

    def used(starting: int, end: int, clips: int) -> str:
        return (
            f"Used in this run: starting pictures {starting} of 6, end pictures {end} of 6, "
            f"clips {clips} of 6."
        )

    told = {
        tool: results_of(tool)
        for tool in ("draw_starting_picture", "edit_starting_picture", "make_end_picture", "film")
    }
    assert used(1, 0, 0) in told["draw_starting_picture"][0]
    assert used(2, 0, 0) in told["edit_starting_picture"][0]
    assert used(2, 1, 0) in told["make_end_picture"][0]
    assert used(2, 2, 0) in told["make_end_picture"][1]
    assert used(2, 2, 1) in told["film"][0]
    assert used(3, 2, 1) in told["draw_starting_picture"][1]


def test_a_move_check_refusal_counts_as_a_failed_try_of_the_planned_action(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from agents.scene_agent import move_check
    from agents.scene_agent.instructions import INSTRUCTIONS

    from .test_scene_move_check import BLOCKED

    fake_model.respond("check_move", BLOCKED)
    fake_model.respond("scene_agent", turn(calls=[DRAW]), turn(calls=[FILM_IT]), GIVE_UP)
    calling(fake_model, say, START)
    run(fake_model, steps)

    [refused] = results_of("film")
    assert move_check.MOVE_FAILED in refused
    assert "This counts as a failed try of the planned action" in refused
    assert "work down the ladder's steps" in refused
    assert "code refuses its move" in " ".join(INSTRUCTIONS.split())
    assert paid_for().count("make_broll_clip") == 0


def test_step_one_is_a_bigger_change_at_the_first_failed_picture_in_both_places(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from agents.scene_agent.instructions import INSTRUCTIONS

    fake_model.respond("check_scene_picture", FAILED_PICTURE)
    fake_model.respond("scene_agent", turn(calls=[DRAW]), GIVE_UP)
    calling(fake_model, say, START)
    run(fake_model, steps)

    [failed] = results_of("draw_starting_picture")
    for told in (" ".join(INSTRUCTIONS.split()), " ".join(failed.split())):
        assert "keeps coming out wrong" not in told
        step_one = told[told.index("1. ") : told.index("2. ")]
        # A wrong part the scene doesn't need is hidden right away, never spot-edited.
        assert "doesn't need" in step_one
        assert "hide it right away" in step_one
        for way in ("angle it away", "frame it out", "natural grip"):
            assert way in step_one, way
        assert "don't spot-edit it" in step_one
        assert "Never invent it" in step_one
        # A wrong part the scene needs is never hidden: closer, or a fresh drawing.
        assert "the scene needs" in step_one
        assert "never hide it" in step_one
        assert "closer to the camera" in step_one
        assert "fresh drawing, not an edit" in step_one


def test_a_try_a_note_superseded_mid_render_reports_the_new_runs_tries(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from agents.scene_agent import move_check

    from .test_scene_move_check import BLOCKED

    def note_during_render() -> dict[str, object]:
        fake_model.respond(
            "produce",
            turn(
                calls=[
                    (
                        "pass_note",
                        {
                            "scenes": [
                                {
                                    "scene": 2,
                                    "instruction": "Keep the handle in view.",
                                    "quotes": ["Show its handle"],
                                }
                            ]
                        },
                    )
                ]
            ),
            turn(says="I passed it on."),
        )
        say("Show its handle")
        return {"state": "working"}

    fake_model.respond("collect_clip", note_during_render)
    fake_model.respond("check_move", {**BLOCKED, "blocked": False}, BLOCKED)
    fake_model.respond(
        "scene_agent", turn(calls=[DRAW]), turn(calls=[FILM_IT]), turn(calls=[FILM_IT]), GIVE_UP
    )
    calling(fake_model, say, START)
    fake_model.answer_unscripted("produce", lambda _: turn(says="The scene is ready."))
    steps.run_held()

    superseded, refused = results_of("film")
    # The note's new run has used nothing yet: the old run's clip isn't counted to it.
    nothing_used = "Used in this run: starting pictures 0 of 6, end pictures 0 of 6, clips 0 of 6."
    assert nothing_used in superseded
    # A move-check refusal carries the tries line too.
    assert move_check.MOVE_FAILED in refused
    assert nothing_used in refused
