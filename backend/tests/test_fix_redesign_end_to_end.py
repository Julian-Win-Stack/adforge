"""Owner notes, looks, scene-agent fixes and version choices through the whole chat."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from agents.models import SystemNote, ToolCall
from chat.models import Attachment, Message
from gateway.fake import FakeModel, turn
from jobs import work
from jobs.library import OwnerPhoto, library_of
from jobs.models import AgentFix, Job, ProducedItem, Scene, SceneStep

from .conftest import HeldSteps, facts_ok, handoffs, paid_for, picture, results_of
from .legacy_records import INSTRUCTION, LOOKED, NOTE, WITH_IT
from .scene_script import script_scene_agents
from .test_producer_auto_fix import FLOATS, failing
from .test_producer_broll import calling, checked, made_ready, run  # noqa: F401
from .test_producer_picture_then_clip import ads, clip
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM

pytestmark = [pytest.mark.django_db(transaction=True)]


@pytest.fixture
def ad(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> ProducedItem:
    """A newly made ad with every scene drawn and filmed by its H3 scene agent."""
    calling(fake_model, say, ("create_music", {"mood": "light upbeat lo-fi"}))
    made_ready(fake_model, steps, say, (1, 2, 3))
    script_scene_agents(fake_model, scenes=3)
    calling(
        fake_model,
        say,
        *[
            (
                "start_scene_agent",
                {"scene": number, "instruction": "Film the planned action.", "quotes": ["Go on"]},
            )
            for number in (1, 2, 3)
        ],
    )
    run(fake_model, steps)
    calling(fake_model, say, ("assemble_ad", {}))
    return Job.objects.get().produced.get(kind="finished_ad")


def asks_the_look_then_fixes() -> list[tuple[str, dict[str, Any]]]:
    return [
        ("look", {"scene": 2, "what": "clip", "number": None, "question": "Which brush?"}),
        ("pass_note", {"scenes": [{"scene": 2, "instruction": INSTRUCTION, "quotes": [NOTE]}]}),
    ]


def owner_note(fake_model: FakeModel, say: Callable[..., None]) -> SceneStep:
    fake_model.respond("look", LOOKED)
    fake_model.respond(
        "produce", turn(calls=asks_the_look_then_fixes()), turn(says="I passed it on.")
    )
    say(NOTE)
    return SceneStep.objects.filter(kind="scene_agent", scene__number=2).latest("started_at", "pk")


def test_a_clip_note_is_fixed_by_the_producer_then_gone_back_for_nothing(
    fake_model: FakeModel,
    ad: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    first = work.versions(Scene.objects.get(number=2))[0]
    step = owner_note(fake_model, say)
    assert steps.held == [step.pk]
    script_scene_agents(fake_model)
    fake_model.respond("produce", turn(calls=[("assemble_ad", {})]), turn(says="Here it is."))
    steps.run_held()
    fixing = AgentFix.objects.get(step=step)
    assert fixing.passed is not None and clip(2) == fixing.passed
    assert len(ads()) == 2
    assert [cut["clip"] for cut in ads()[-1].cuts if cut["scene"] == 2] == [fixing.passed.pk]
    assert results_of("assemble_ad")[-1].count("what it cost") == 1
    paid = paid_for()
    fake_model.respond(
        "produce",
        turn(calls=[("use_version", {"scene": 2, "version": 1})]),
        turn(says="Scene 2 is back to the first version."),
    )
    say("I liked the first one better.")
    assert paid_for() == paid
    assert first.clip is not None and clip(2).file == first.clip.file
    assert len(ads()) == 3


def test_a_question_while_a_scene_runs_is_the_producers_and_the_scene_carries_on(
    fake_model: FakeModel,
    ad: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    step = owner_note(fake_model, say)
    fake_model.respond("produce", turn(says="Scene 2 is still working."))
    say("How is scene 2 coming along?")
    produced = [each for each in fake_model.turns_taken if each.purpose == "produce"][-1]
    assert "How is scene 2 coming along?" in str(produced.handoff)
    assert ToolCall.objects.filter(tool="pass_note").count() == 1
    script_scene_agents(fake_model)
    run(fake_model, steps)
    assert AgentFix.objects.get(step=step).passed is not None


def test_a_failed_clip_is_fixed_by_changing_the_scene_in_the_same_chat(
    fake_model: FakeModel,
    ad: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .test_scene_agent_changes import change

    step = owner_note(fake_model, say)
    new_line = "This mug holds three hundred and fifty millilitres of tea."
    action = "Tea is stirred in the mug with a spoon."
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond("check_clip_product", failing(FLOATS))
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[change(line=new_line, shows=action)]),
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
    )
    fake_model.respond("produce", turn(calls=[("assemble_ad", {})]), turn(says="Here it is."))
    steps.run_held()
    fixing = AgentFix.objects.get(step=step)
    assert fixing.passed is not None
    assert (Scene.objects.get(number=2).line, Scene.objects.get(number=2).shows) == (
        new_line,
        action,
    )
    assert FLOATS in str(handoffs("scene_agent")[-1])
    assert new_line in handoffs("make_broll_clip")[-1]["prompt"]
    assert [cut["clip"] for cut in ads()[-1].cuts if cut["scene"] == 2] == [fixing.passed.pk]


def test_the_note_is_looked_at_with_its_starting_picture_and_passed_as_exact_quotes(
    fake_model: FakeModel,
    ad: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    noted = clip(2)
    paid = len(paid_for())
    step = owner_note(fake_model, say)
    looked = next(each for each in fake_model.asked if each.purpose == "look")
    assert noted.picture is not None
    assert looked.images[0].key == noted.picture.file
    assert "starting picture" in looked.images[0].label.lower()
    assert paid_for()[paid:] == ["look"]
    script_scene_agents(fake_model)
    run(fake_model, steps)
    fixing = AgentFix.objects.get(step=step)
    assert fixing.passed is not None
    request = [each for each in fake_model.turns_taken if each.purpose == "scene_agent"][-1]
    assert NOTE in str(request.handoff) and INSTRUCTION in str(request.handoff)
    assert "Owner's words (checked word for word)" in str(request.handoff)
    assert not [each for each in fake_model.turns_taken if each.purpose == "fix_scene"]


GREEN_PHOTO = "Here it is, the green one."
PHOTO_QUESTION = "Could you send a photo of the green brush?"


def test_a_scene_question_is_asked_once_and_a_photo_answer_opens_a_new_run(
    fake_model: FakeModel,
    ad: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    step = owner_note(fake_model, say)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("ask_owner", {"question": PHOTO_QUESTION, "wants": "photo"})]),
        turn(calls=[("give_up", {"reason": "A product view is missing."})]),
    )
    run(fake_model, steps)
    assert Message.objects.filter(role="notice", text__contains=PHOTO_QUESTION).count() == 1
    fake_model.respond(
        "produce",
        turn(
            calls=[
                (
                    "pass_note",
                    {"scenes": [{"scene": 2, "instruction": WITH_IT, "quotes": [GREEN_PHOTO]}]},
                )
            ]
        ),
        turn(says="Thanks."),
    )
    say(GREEN_PHOTO, ("green.png", picture(40, 30, (20, 160, 40))))
    script_scene_agents(fake_model)
    run(fake_model, steps)
    attachment = Attachment.objects.get(message__text=GREEN_PHOTO)
    item = next(
        each
        for each in library_of(Job.objects.get())
        if each.source == OwnerPhoto(original=attachment.file)
    )
    assert item.file != attachment.file
    request = [each for each in fake_model.turns_taken if each.purpose == "scene_agent"][-1]
    assert GREEN_PHOTO in str(request.handoff) and WITH_IT in str(request.handoff)
    assert f"library item {item.number}" in str(request.handoff)
    assert work.open_question(Scene.objects.get(number=2)) is None
    assert AgentFix.objects.get(step=step).scene_runs.count() == 2
    cards = [
        message["question"]
        for message in api.get(f"/api/sessions/{session_id}/messages/").json()
        if message["question"] is not None
    ]
    assert cards and all(not each["open"] for each in cards)


def test_pause_finishes_the_running_try_and_next_message_carries_the_scene_on(
    fake_model: FakeModel,
    ad: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    step = owner_note(fake_model, say)
    paid = paid_for().count("make_broll_clip")

    def paused_while_judged() -> dict[str, Any]:
        fake_model.respond("produce", turn(says="The current clip is finishing."))
        assert api.post(f"/api/sessions/{session_id}/pause/", {}, format="json").status_code == 200
        return failing(FLOATS)

    fake_model.respond("check_clip_product", paused_while_judged)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("film", FILM)]),
    )
    run(fake_model, steps)
    assert paid_for().count("make_broll_clip") == paid + 1
    assert fake_model.left("scene_agent") == 1
    assert AgentFix.objects.get(step=step).paused_at is not None
    assert SystemNote.objects.filter(kind=SystemNote.Kind.PAUSED).exists()
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
                                "instruction": "Carry on with the scene.",
                                "quotes": ["Go on with the scene"],
                            }
                        ]
                    },
                )
            ]
        ),
        turn(says="Carrying on."),
    )
    say("Go on with the scene")
    fake_model.respond("produce", turn(calls=[("assemble_ad", {})]), turn(says="Here it is."))
    steps.run_held()
    assert paid_for().count("make_broll_clip") == paid + 2
    assert Scene.objects.get(number=2).status == Scene.Status.FINISHED
    assert len(ads()) == 2
