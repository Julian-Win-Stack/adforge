"""Review regressions through the owner chat and paid-model handoffs."""

from collections.abc import Callable

import pytest

from agents import pause, tasks
from chat.models import Session
from gateway.fake import FakeModel, turn

from .conftest import HeldSteps, WorkerStopped, facts_ok, handoffs, paid_for, results_of
from .test_broll_sent_list_end_to_end import page_with_a_video  # noqa: F401
from .test_picker_labels import labelled_page  # noqa: F401
from .test_producer_broll import SHOWS, calling, checked, run  # noqa: F401
from .test_product_videos import picks_videos  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_agent_changes import change
from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES
from .test_scene_reference import video_scene_ready  # noqa: F401

pytestmark = [pytest.mark.django_db(transaction=True)]

# What scene 2 becomes when its agent makes it talking: the one middle talking scene the
# whole-plan rules allow (jobs.plan_rules), unlike scene 1's selfie out of its box.
TALKING_2 = {"talking_style": "talking while doing it", "product_state": "in use"}


def broll_before_the_last(fake_model: FakeModel, say: Callable[..., None], shows: str) -> None:
    """The owner has a B-roll scene added before the last, which is always talking, so the
    plan keeps a B-roll scene when scene 2's agent makes it talking (jobs.plan_rules). The last
    scene moves beside its box first, so its product state differs from both scene 1's, out of
    its box, and the "in use" scene 2 takes."""
    calling(
        fake_model,
        say,
        ("edit_scene", {"scene": 3, "product_state": "beside its box", "packaging_photo": 1}),
        (
            "add_scene",
            {
                "at": 3,
                "line": "Every single detail is made to last you for years and years to come.",
                "kind": "broll",
                "kind_why": "A close view of the product is needed.",
                "shows": shows,
                "broll_kind": "showcase",
                "person_shown": "no face",
            },
        ),
    )
    for tool in ("edit_scene", "add_scene"):
        assert "Refused:" not in results_of(tool)[-1], results_of(tool)[-1]


def test_restart_during_pause_finishes_started_draw_but_refuses_unstarted_draw(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
) -> None:
    fake_model.respond("check_scene_picture", WorkerStopped(), PICTURE_PASSES, PICTURE_PASSES)
    fake_model.respond(
        "scene_agent",
        turn(
            calls=[
                ("draw_starting_picture", PICTURE_FORM),
                ("draw_starting_picture", {**PICTURE_FORM, "pose": "Hands beside it"}),
            ]
        ),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["Go on"]}),
    )
    with pytest.raises(WorkerStopped):
        steps.run_held()
    pause.pause(Session.objects.get(pk=session_id), wake=False)
    fake_model.respond("produce", turn(says="Paused."))
    tasks.carry_on_after_the_worker_starts()
    steps.run_held()
    assert paid_for().count("draw_scene_picture") == 1
    assert "parked for the owner's pause" in str(handoffs("produce")[-1])


def test_drawing_uses_the_scene_face_choice_after_a_row_change(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    broll_before_the_last(fake_model, say, "A hand sets the mug down on the table.")
    fake_model.respond("produce", turn(says="I'll use the corrected appearance."))
    say("The true colour is blue.")
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond("check_scene_picture", PICTURE_PASSES)
    fake_model.respond(
        "scene_agent",
        turn(calls=[change(shows=SHOWS, kind="talking", person_shown="has face", **TALKING_2)]),
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("give_up", {"reason": "Need another angle."})]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": 2, "instruction": "Show the presenter.", "quotes": ["Go on"]},
        ),
    )
    run(fake_model, steps)
    assert "shows the person and room" in handoffs("draw_scene_picture")[-1]["prompt"]
    given = next(
        request for request in fake_model.asked if request.purpose == "check_scene_picture"
    )
    assert "The true colour is blue." in handoffs("check_scene_picture")[-1]["owner_chat"]
    assert "Use the owner's chat only for how the product looks" in given.instructions
    assert "an owner's correction about appearance beats the page" in given.instructions
    agent = next(request for request in fake_model.turns_taken if request.purpose == "scene_agent")
    assert "Every B-roll scene does something with the product that sells it" in agent.instructions
    assert "Finishing steps" in agent.instructions
    assert "two-state claim needs two" in agent.instructions
    assert "camera never moves" not in agent.instructions.lower()


@pytest.mark.without_end_card_rules
def test_owner_answer_run_restores_photo_and_video_choices_after_an_action_gives_up(
    fake_model: FakeModel,
    video_scene_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .conftest import picture
    from .legacy_records import LOOKED
    from .test_broll_sent_list_end_to_end import LINE
    from .test_scene_agent_questions import ASK

    def inspect_references() -> list[str]:
        fake_model.respond("look", LOOKED)
        calling(
            fake_model,
            say,
            (
                "look",
                {
                    "scene": 2,
                    "what": "place",
                    "number": None,
                    "question": "Which picture and video references does this scene keep?",
                },
            ),
        )
        return [
            line
            for line in handoffs("look")[-1]["scene"].splitlines()
            if line.startswith(("Its pictures:", "Its video piece:"))
        ]

    broll_before_the_last(fake_model, say, "A hand sets the shampoo bottle down on the shelf.")

    fake_model.respond(
        "scene_agent",
        turn(calls=[ASK]),
        turn(calls=[("give_up", {"reason": "Need another view."})]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {
                "scene": 2,
                "instruction": "Show its use.",
                "quotes": ["Go on"],
            },
        ),
    )
    run(fake_model, steps)
    before = inspect_references()
    assert len(before) == 2
    assert "video 1, moment 1" in before[1]

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
                                "instruction": "Use this view.",
                                "quotes": ["Here is the missing view."],
                            }
                        ]
                    },
                )
            ]
        ),
        turn(says="Thanks."),
    )
    say("Here is the missing view.", ("view.png", picture(40, 30, (30, 100, 30))))
    fake_model.respond("fact_check", facts_ok(2))
    changed = change(
        line=LINE,
        shows="A hand smooths shampoo through wet hair.",
        kind="talking",
        person_shown="has face",
        **TALKING_2,
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[changed]),
        turn(calls=[changed]),
        turn(calls=[("give_up", {"reason": "The action still cannot be made."})]),
    )
    run(fake_model, steps)
    changed_context = handoffs("scene_agent")[-1]["conversation"][0]["text"]
    scene_two = changed_context.split("Scene 2\n", 1)[1].split("Scene 3\n", 1)[0]
    assert "scene kind: talking" in scene_two
    assert "its pictures:" not in scene_two
    assert "Draw the product in the page video's colour:" not in changed_context
    assert "The scene row changed" in str(handoffs("scene_agent")[-1])
    assert inspect_references() == before
