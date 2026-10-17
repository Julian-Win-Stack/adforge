"""The shop owner's notes, once the ad has a clip, through the chat (fix redesign, ticket 08;
decisions 1, 2, 4, 7, 14, 15 and 20; flow steps 1-2, 7 and 9). Every message goes to the
decider first, by code; its plan is shown by code with a yes button; the yes, tapped or
typed, is read by code and runs the plan; the producer only answers what isn't a note, from
the system notes code writes. Models are faked at the gateway, the clips are real tiny
videos and ffmpeg runs for real. The producer's model is scripted only where it answers,
so any other producer turn would fail the test."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from agents import loop
from agents.models import SystemNote, ToolCall
from agents.producer import PRODUCER
from chat.models import Message, Session
from gateway.fake import FakeModel, turn
from gateway.types import StepFinished, Turn
from jobs import work
from jobs.models import FixCheck, FixPlan, ProducedItem, ReferencePhoto, Scene, SceneStep

from .conftest import MUG_SIDE, HeldSteps, producer_turns
from .fixing import fixed
from .test_fix_check import HANDLE_HIDDEN, not_made
from .test_fix_loop import TURN_REWRITE
from .test_producer_broll import TALKING_CHOICE, checked  # noqa: F401 (a fixture)
from .test_producer_picture_then_clip import (
    ads,
    assembled,  # noqa: F401 (a fixture)
    clip,
    picture,
    status,
)

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("checked", "assembled"),
]

BRIGHTER = "Make the workshop brighter."
TURN = "The hand turns the mug all the way round."
NEW_LINE = "Stoneware that keeps your tea hot."
LABEL = f"scene 1 v2 {BRIGHTER}"
LIFT = "The hand lifts the mug to the camera."


def sort(kind: str) -> tuple[str, dict[str, Any]]:
    return ("sort_message", {"kind": kind})


def row(scene: int, action: str, change: str = "", **fields: Any) -> dict[str, Any]:
    return {
        "scene": scene,
        "action": action,
        "change": change,
        "line": "",
        "go_back_to": None,
        "cant": "",
        "photos": [],
        **fields,
    }


def decided(*rows: dict[str, Any], **fields: Any) -> tuple[str, dict[str, Any]]:
    return ("decide", {"scenes": list(rows), "photos": [], "cants": [], "question": "", **fields})


def planning(*calls: tuple[str, dict[str, Any]]) -> Turn:
    """The decider's turn for a note: sorted, looked at, decided."""
    return turn(calls=[sort("fix_note"), *calls])


def plans() -> list[FixPlan]:
    return list(FixPlan.objects.order_by("id"))


def notices(event: str) -> list[Message]:
    return list(Message.objects.filter(plan_event=event).order_by("seq"))


def tap_yes(api: APIClient, session_id: str, plan: FixPlan) -> None:
    """The shop owner taps the plan's yes button."""
    sent = api.post(
        f"/api/sessions/{session_id}/messages/", {"text": "Yes", "plan": plan.pk}, format="json"
    )
    assert sent.status_code == 201, sent.json()


def given_to_the_producer(fake_model: FakeModel) -> list[str]:
    """The system notes the producer's latest turn was given, as it read them."""
    produced = [each for each in fake_model.turns_taken if each.purpose == "produce"]
    return [
        each.text
        for each in produced[-1].handoff.conversation
        if isinstance(each, StepFinished) and each.text.startswith(loop.SYSTEM_NOTE)
    ]


def a_picture_note_planned(fake_model: FakeModel, say: Callable[..., None]) -> FixPlan:
    fake_model.respond(
        "decide_fixes",
        planning(("look_at_picture", {"scene": 1}), decided(row(1, "new_picture", BRIGHTER))),
    )
    say("Scene 1 looks dark. Make the workshop brighter.")
    [plan] = plans()
    return plan


@pytest.fixture
def held(steps: HeldSteps, fake_model: FakeModel) -> HeldSteps:
    return steps


def test_a_picture_note_is_shown_as_a_plan_and_run_by_code_once_the_yes_is_tapped(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    turns, ads_before = producer_turns(), len(ads())

    plan = a_picture_note_planned(fake_model, say)

    # Code showed the plan, with no producer turn, and made nothing before the yes.
    [shown] = notices("shown")
    assert shown.fix_plan == plan and plan.state == "proposed"
    assert producer_turns() == turns
    assert held.held == []

    tap_yes(api, session_id, plan)

    plan.refresh_from_db()
    assert plan.state == "confirmed"
    assert plan.confirmed_by == Message.objects.get(plan_event="yes")
    assert len(notices("confirmed")) == 1
    [picture_step] = SceneStep.objects.filter(pk__in=held.held)
    assert (picture_step.kind, picture_step.scene.number) == ("starting_picture", 1)
    assert picture_step.note == BRIGHTER

    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    held.run_next()

    # The picture passed its checks, so code queued its clip: no producer turn in between.
    [clip_step] = SceneStep.objects.filter(pk__in=held.held)
    assert (clip_step.kind, clip_step.picture) == ("clip", picture(1))
    assert producer_turns() == turns
    held.run_held()

    assert status(1) == "finished"
    assert len(ads()) == ads_before + 1
    assert ads()[-1].cuts[0]["clip"] == clip(1).pk
    # Shown labelled with the scene version it was made for, as the scene cards are.
    [shown_ad] = Message.objects.filter(attachments__file=ads()[-1].file)
    assert shown_ad.text == f"Ad version {ads()[-1].version}, with {LABEL}."
    assert producer_turns() == turns
    assert not Session.objects.get().producer_running
    assert list(SystemNote.objects.values_list("kind", flat=True)) == [
        "plan_shown",
        "fixes_started",
        "scene_done",
        "ad_shown",
    ]


@pytest.mark.parametrize("typed", ["yes", "yeah go"])
def test_a_typed_yes_the_decider_reads_runs_the_plan(
    fake_model: FakeModel, held: HeldSteps, say: Callable[..., None], typed: str
) -> None:
    turns = producer_turns()
    plan = a_picture_note_planned(fake_model, say)
    fake_model.respond("decide_fixes", turn(calls=[sort("yes")]))

    say(typed)

    plan.refresh_from_db()
    assert plan.state == "confirmed"
    assert plan.confirmed_by == Message.objects.filter(role="user").last()
    assert [step.kind for step in SceneStep.objects.filter(pk__in=held.held)] == [
        "starting_picture"
    ]
    assert producer_turns() == turns


def test_a_typed_no_drops_the_plan_and_the_producer_is_told(
    fake_model: FakeModel, held: HeldSteps, say: Callable[..., None]
) -> None:
    plan = a_picture_note_planned(fake_model, say)
    fake_model.respond("decide_fixes", turn(calls=[sort("no")]))
    fake_model.respond("produce", turn(says="No problem, nothing will change."))

    say("no, leave it")

    plan.refresh_from_db()
    assert plan.state == "declined"
    assert len(notices("declined")) == 1
    assert held.held == []
    told = given_to_the_producer(fake_model)
    assert (
        told[-1]
        == f"{loop.SYSTEM_NOTE} The shop owner said no to the plan, so nothing in it was changed."
    )
    assert Message.objects.filter(role="agent").last().text == "No problem, nothing will change."  # type: ignore[union-attr]


def test_a_correction_shows_a_new_plan_in_place_of_the_first(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    first = a_picture_note_planned(fake_model, say)
    # The looks already made this round are kept: the decider decides again without them.
    fake_model.respond(
        "decide_fixes",
        planning(decided(row(1, "new_picture", "Brighter, with the window open."))),
    )

    say("No, open the window so the light comes in.")

    first.refresh_from_db()
    second = plans()[-1]
    assert first.state == "replaced" and second.state == "proposed"
    assert [each.fix_plan for each in notices("shown")] == [first, second]
    # The first plan's button is refused now; the second's runs.
    refused = api.post(
        f"/api/sessions/{session_id}/messages/", {"text": "Yes", "plan": first.pk}, format="json"
    )
    assert refused.status_code == 400
    tap_yes(api, session_id, second)
    assert SceneStep.objects.get(pk=held.held[0]).note == "Brighter, with the window open."


def test_a_question_mid_plan_is_answered_by_the_producer_from_the_notes_and_the_plan_stays(
    fake_model: FakeModel, held: HeldSteps, say: Callable[..., None]
) -> None:
    plan = a_picture_note_planned(fake_model, say)
    # Not scripted for the decider: it sorts the question as not a fix.
    fake_model.respond("produce", turn(says="About $0.55, up to $1.10, once you say yes."))

    say("What's the cost?")

    plan.refresh_from_db()
    assert plan.state == "proposed"
    assert held.held == []
    [told] = given_to_the_producer(fake_model)
    assert told.startswith(f"{loop.SYSTEM_NOTE} A fix plan is shown to the shop owner")
    assert BRIGHTER in told
    # The producer has no tool to decide or make a fix with.
    produced = [each for each in fake_model.turns_taken if each.purpose == "produce"][-1]
    names = {tool.name for tool in produced.tools}
    assert not names & {"restate_fix", "change_line"}


def test_the_producer_answers_whats_happening_from_the_notes_without_asking_again(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    plan = a_picture_note_planned(fake_model, say)
    tap_yes(api, session_id, plan)
    fake_model.respond("produce", turn(says="Scene 1's new starting picture is being made."))

    say("What's happening with scene 1?")

    told = given_to_the_producer(fake_model)
    assert [text.split(":", 1)[1].split(".")[0].strip() for text in told] == [
        "A fix plan is shown to the shop owner, waiting for their yes",
        "The shop owner said yes to the plan: scene 1's new starting picture is being made",
    ]
    # It answered, and started nothing: no tool call, no new step.
    assert not ToolCall.objects.filter(agent="producer", created_at__gt=plan.created_at)
    assert len(held.held) == 1


def test_a_yes_tapped_on_a_plan_replaced_meanwhile_runs_nothing_and_the_producer_says_so(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    first = a_picture_note_planned(fake_model, say)
    # The tap is kept while the plan waits; a correction then replaces it before code
    # reads the tap.
    tap = Message.objects.create(
        session=Session.objects.get(),
        seq=Message.objects.order_by("seq").last().seq + 1,  # type: ignore[union-attr]
        role="user",
        text="Yes",
        plan_event="yes",
        fix_plan=first,
    )
    first.state = FixPlan.State.REPLACED
    first.save(update_fields=["state"])
    fake_model.respond("produce", turn(says="That plan changed: look at the newer one."))

    say("Hello?")

    tap.refresh_from_db()
    assert tap.routed_at is not None
    assert held.held == []
    assert any("a newer one had replaced" in text for text in given_to_the_producer(fake_model))


def test_an_owner_photo_in_the_note_goes_with_the_clip_in_reference_mode(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    uses = [{"photo": 1, "in_picture": False, "during_action": True}]
    fake_model.respond(
        "decide_fixes",
        planning(
            ("look_at_owner_photo", {"photo": 1}),
            ("look_at_clip", {"scene": 2, "version": None}),
            decided(
                row(2, "new_clip", "The hand pours tea into the black mug.", photos=uses),
                photos=[{"photo": 1, "thing": "black mug", "clear": True}],
            ),
        ),
    )
    say("Pour the tea into my black mug in scene 2.", ("mug.png", MUG_SIDE))
    [plan] = plans()

    tap_yes(api, session_id, plan)

    [used] = ReferencePhoto.objects.filter(scene__number=2)
    assert (used.thing, used.use) == ("black mug", "clip")
    assert used.added_by == plan.scenes.get().fix
    [step] = SceneStep.objects.filter(pk__in=held.held)
    assert step.kind == "clip"
    assert list(step.reference_photos.all()) == [used]
    fake_model.respond("rewrite_clip_prompt", TURN_REWRITE)
    held.run_held()
    # The clip was made in reference mode: its drawn starting picture first, then the clean
    # copy of the shop owner's thing.
    asked = fake_model.broll_clips_asked[-1]
    assert asked["starting_picture"] is None
    assert len(asked["example_pictures"]) == 2
    assert status(2) == "finished"


def test_going_back_to_the_first_version_is_free_and_lands_at_once(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    plan = a_picture_note_planned(fake_model, say)
    tap_yes(api, session_id, plan)
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    held.run_held()
    first = work.versions(Scene.objects.get(number=1))[0]
    ads_before = len(ads())
    fake_model.respond(
        "decide_fixes",
        planning(
            ("look_at_version", {"scene": 1, "version": 1}),
            decided(row(1, "go_back", go_back_to=1)),
        ),
    )
    say("I liked the first one better.")
    back = plans()[-1]
    assert back.scenes.get().estimate_usd == 0

    tap_yes(api, session_id, back)

    assert held.held == []
    assert clip(1).file == first.clip.file  # type: ignore[union-attr]
    assert work.versions(Scene.objects.get(number=1))[-1].label == "v3 back to v1"
    assert len(ads()) == ads_before + 1


def test_a_line_note_runs_the_line_chain_by_code(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    turns = producer_turns()
    fake_model.respond(
        "decide_fixes",
        planning(
            ("look_at_picture", {"scene": 3}),
            decided(row(3, "new_line", "Say it warmer.", line=NEW_LINE)),
        ),
    )
    say(f'Scene 3 should say "{NEW_LINE}"')
    [plan] = plans()

    tap_yes(api, session_id, plan)
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    held.run_held()

    assert Scene.objects.get(number=3).line == NEW_LINE
    made = clip(3)
    assert made.step is not None and made.step.line == NEW_LINE
    assert ads()[-1].cuts[-1]["clip"] == made.pk
    assert producer_turns() == turns


def test_go_ahead_to_a_failed_fix_check_carries_its_scene_on(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    plan = a_picture_note_planned(fake_model, say)
    tap_yes(api, session_id, plan)
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    fake_model.respond("check_fix", not_made(HANDLE_HIDDEN), not_made(HANDLE_HIDDEN))
    held.run_held()
    assert FixCheck.objects.get().verdict == "failed"
    assert not SceneStep.objects.filter(scene__number=1, kind="clip", plan_scene__isnull=False)
    ads_before = len(ads())
    fake_model.respond("decide_fixes", turn(calls=[sort("go_ahead")]))

    say("That's fine, go ahead.")

    assert FixCheck.objects.get().gone_ahead_at is not None
    [step] = SceneStep.objects.filter(pk__in=held.held)
    assert (step.kind, step.scene.number) == ("clip", 1)
    held.run_held()
    assert status(1) == "finished"
    assert len(ads()) == ads_before + 1


def test_the_decider_is_told_of_a_failed_fix_check_and_go_ahead_needs_one(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    # Go ahead with no fix check waiting is refused, so the message isn't lost: the decider
    # sorts it again.
    fake_model.respond(
        "decide_fixes", turn(calls=[sort("go_ahead")]), turn(calls=[sort("not_a_fix")])
    )
    fake_model.respond("produce", turn(says="Glad you like it."))
    say("Go ahead.")
    assert ToolCall.objects.filter(
        agent="decider", tool="sort_message", result__startswith="Refused:"
    ).get().result == (
        "Refused: no fix check is waiting for the shop owner's go ahead. Nothing was done."
    )

    plan = a_picture_note_planned(fake_model, say)
    tap_yes(api, session_id, plan)
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    fake_model.respond("check_fix", not_made(HANDLE_HIDDEN), not_made(HANDLE_HIDDEN))
    held.run_held()
    fake_model.respond(
        "decide_fixes",
        planning(
            ("look_at_picture", {"scene": 1}),
            decided(row(1, "new_picture", "The handle faces the camera.")),
        ),
    )

    say("The handle should face the camera.")

    ad_told = [each for each in fake_model.turns_taken if each.purpose == "decide_fixes"][-1]
    the_ad = ad_told.handoff.conversation[0].text  # type: ignore[union-attr]
    assert f"Scene 1's fix check failed on its new picture: {HANDLE_HIDDEN}" in the_ad
    # Anything but go ahead is planned again.
    assert plans()[-1].state == "proposed"


def test_a_decider_that_gives_no_answer_leaves_the_message_to_the_producer(
    fake_model: FakeModel, held: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond(
        "decide_fixes", turn(says="Hmm."), turn(says="Let me think."), turn(says="Well.")
    )
    fake_model.respond("produce", turn(says="Could you say which scene?"))

    say("Make it pop.")

    assert Message.objects.filter(role="agent").last().text == "Could you say which scene?"  # type: ignore[union-attr]
    assert Message.objects.filter(role="user").last().routed_at is not None  # type: ignore[union-attr]
    assert plans() == []


def test_messages_from_before_the_first_clip_are_the_producers_and_never_the_deciders(
    fake_model: FakeModel, held: HeldSteps
) -> None:
    # The whole first making went through the chat: none of it was routed to the decider,
    # and every message after the first clip was, each passed on as not a fix.
    first_clip = ProducedItem.objects.filter(kind="clip").order_by("created_at").first()
    assert first_clip is not None
    said = Message.objects.filter(role="user")
    assert not said.filter(created_at__lt=first_clip.created_at, routed_at__isnull=False)
    assert not said.filter(created_at__gt=first_clip.created_at, routed_at__isnull=True)
    sorted_ = ToolCall.objects.filter(agent="decider", tool="sort_message")
    assert sorted_.count() == said.filter(created_at__gt=first_clip.created_at).count()
    assert PRODUCER.reads_system_notes


def test_two_plans_on_a_scene_each_show_their_own_labelled_ad(
    fake_model: FakeModel, held: HeldSteps
) -> None:
    # Design thread, 03:15 Oct 17: the owner says yes to a second plan on scene 2 before the
    # first plan's clip lands. Each plan shows its own ad, labelled, so both are versions.
    ads_before = len(ads())
    fake_model.respond("rewrite_clip_prompt", TURN_REWRITE, TURN_REWRITE)
    first = fixed(2, "new_clip", change=TURN)
    second = fixed(2, "new_clip", change=LIFT)
    held.run_held()

    assert [ad.version for ad in ads()[ads_before:]] == [2, 3]
    shown = [Message.objects.get(attachments__file=ad.file).text for ad in ads()[ads_before:]]
    assert shown == [
        f"Ad version 2, with scene 2 v2 {TURN}.",
        f"Ad version 3, with scene 2 v3 {LIFT}.",
    ]
    assert [plan.system_notes.filter(kind="ad_shown").count() for plan in (first, second)] == [
        1,
        1,
    ]
