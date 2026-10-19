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
from django.utils import timezone
from rest_framework.test import APIClient

from agents import decider, loop, routing, run_plan
from agents.models import SystemNote, ToolCall
from chat import messages
from chat.models import Message, Session
from gateway.fake import FakeModel, meanwhile, turn
from gateway.types import StepFinished, Turn, UnusableReply
from jobs import fix_check, work
from jobs.models import (
    FixCheck,
    FixPlan,
    FixPlanScene,
    Job,
    ProducedItem,
    ReferencePhoto,
    Scene,
    SceneStep,
)

from .agent_tries import agent_films
from .conftest import CLIP_SIDES_OK, MUG_SIDE, HeldSteps, producer_turns
from .fixing import fixed, owner_photo, plan_row, planned
from .test_fix_check import HANDLE_HIDDEN, not_made
from .test_fix_loop import TURN_REWRITE
from .test_producer_auto_fix import FLOATS, REMADE, REMADE_AGAIN, failing
from .test_producer_broll import (  # noqa: F401 (a fixture)
    BROLL_CHOICE,
    MOTION,
    TALKING_CHOICE,
    checked,
)
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


def stored(*rows: FixPlanScene, note: str) -> FixPlan:
    """A plan stored before the fixing agent, with B-roll rows the decider no longer answers,
    shown and waiting for its yes; its note was routed, as the chat routed it then."""
    plan = planned(*rows, note=note)
    Message.objects.filter(role="user", routed_at__isnull=True).update(routed_at=timezone.now())
    return plan


def mug_photo_sent(fake_model: FakeModel, say: Callable[..., None], text: str) -> dict[str, Any]:
    """The shop owner's black mug photo sent in `text`, which the producer answers; the plan
    row's photo of it, its thing only during the action."""
    fake_model.respond("produce", turn(says="Thanks, I have your photo."))
    say(text, ("mug.png", MUG_SIDE))
    [sent] = messages.owner_photos(Session.objects.get())
    return owner_photo(1, "black mug", sent.file, in_picture=False, during_action=True)


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
    # The question went to the producer, not into a new plan, and nothing new started.
    assert plans() == [plan]
    assert Message.objects.filter(role="agent").last().text == (  # type: ignore[union-attr]
        "Scene 1's new starting picture is being made."
    )
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
    # A plan stored before the fixing agent: a new clip of a B-roll scene, with the photo.
    note = "Pour the tea into my black mug in scene 2."
    mug = mug_photo_sent(fake_model, say, note)
    pours = plan_row(2, "new_clip", change="The hand pours tea into the black mug.", photos=[mug])
    plan = stored(pours, note=note)

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


def test_a_picture_failing_its_fix_check_once_is_drawn_again_and_its_clip_made(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    turns, ads_before = producer_turns(), len(ads())
    plan = a_picture_note_planned(fake_model, say)
    tap_yes(api, session_id, plan)
    fake_model.respond("choose_starting_picture", TALKING_CHOICE, TALKING_CHOICE)
    fake_model.respond("check_fix", not_made(HANDLE_HIDDEN), not_made(HANDLE_HIDDEN))

    held.run_held()

    # The redraw passed: the shop owner was never asked, and its clip made the new ad.
    failed, passed = FixCheck.objects.order_by("id")
    assert (failed.verdict, passed.verdict) == ("failed", "passed")
    assert failed.notice is None
    assert clip(1).picture == passed.new
    assert len(ads()) == ads_before + 1
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
    # The picture fails its fix check every try: drawn again twice, told why, then the shop
    # owner is asked.
    tries = 1 + fix_check.MOST_REDRAWS
    fake_model.respond("choose_starting_picture", *[TALKING_CHOICE] * tries)
    fake_model.respond("check_fix", *[not_made(HANDLE_HIDDEN)] * (2 * tries))
    held.run_held()
    checks = list(FixCheck.objects.order_by("id"))
    assert [check.verdict for check in checks] == ["failed"] * tries
    assert [check.notice is not None for check in checks] == [False] * (tries - 1) + [True]
    assert not SceneStep.objects.filter(scene__number=1, kind="clip", plan_scene__isnull=False)
    ads_before = len(ads())
    fake_model.respond("decide_fixes", turn(calls=[sort("go_ahead")]))

    say("That's fine, go ahead.")

    assert FixCheck.objects.get(notice__isnull=False).gone_ahead_at is not None
    [step] = SceneStep.objects.filter(pk__in=held.held)
    assert (step.kind, step.scene.number) == ("clip", 1)
    held.run_held()
    assert status(1) == "finished"
    assert len(ads()) == ads_before + 1


@pytest.mark.usefixtures("no_fix_redraws")
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


def test_a_tap_after_a_typed_yes_adds_nothing_and_tells_no_one_of_a_newer_plan(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    turns = producer_turns()
    plan = a_picture_note_planned(fake_model, say)
    # The shop owner taps yes too while the decider reads their typed yes.
    fake_model.respond(
        "decide_fixes",
        meanwhile(lambda: tap_yes(api, session_id, plan), turn(calls=[sort("yes")])),
    )

    say("yes")

    plan.refresh_from_db()
    assert plan.state == "confirmed" and plans() == [plan]
    assert not SystemNote.objects.filter(kind="yes_too_late").exists()
    assert Message.objects.get(plan_event="yes").routed_at is not None
    assert producer_turns() == turns
    assert len(held.held) == 1


def test_a_tap_after_a_typed_no_runs_nothing_and_the_producer_is_told_so(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    plan = a_picture_note_planned(fake_model, say)
    fake_model.respond(
        "decide_fixes",
        meanwhile(lambda: tap_yes(api, session_id, plan), turn(calls=[sort("no")])),
    )
    fake_model.respond("produce", turn(says="Nothing changed. Do you want it after all?"))

    say("no")

    plan.refresh_from_db()
    assert plan.state == "declined"
    assert held.held == []
    told = given_to_the_producer(fake_model)
    assert f"{loop.SYSTEM_NOTE} {routing.TAPPED_AFTER_NO}" in told
    assert not any("a newer one had replaced" in text for text in told)


def test_a_typed_yes_to_a_plan_with_only_a_question_runs_nothing_and_keeps_the_note(
    fake_model: FakeModel, held: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond("decide_fixes", planning(decided(question="The blue mug or the green one?")))
    say("Make the mug the other colour.")
    [plan] = plans()
    # A yes to a plan with nothing to run is refused; the decider sorts it again.
    fake_model.respond("decide_fixes", turn(calls=[sort("yes")]), turn(calls=[sort("not_a_fix")]))
    fake_model.respond("produce", turn(says="Which colour would you like?"))

    say("yes")

    assert (
        ToolCall.objects.filter(agent="decider", tool="sort_message", result__startswith="Refused:")
        .get()
        .result.startswith("Refused: the plan waiting has nothing to run")
    )
    plan.refresh_from_db()
    assert plan.state == "proposed"
    assert notices("confirmed") == []
    assert "Make the mug the other colour." in [
        each.text for each in decider.this_round(Job.objects.get())
    ]


@pytest.mark.usefixtures("no_fix_redraws")
def test_a_note_on_a_scene_planned_again_after_its_fix_check_failed_no_longer_waits(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    first = a_picture_note_planned(fake_model, say)
    tap_yes(api, session_id, first)
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    fake_model.respond("check_fix", not_made(HANDLE_HIDDEN), not_made(HANDLE_HIDDEN))
    held.run_held()
    assert FixCheck.objects.get().verdict == "failed"
    fake_model.respond(
        "decide_fixes",
        planning(
            ("look_at_picture", {"scene": 1}),
            decided(row(1, "new_picture", "The handle faces the camera.")),
        ),
    )
    say("The handle should face the camera.")

    tap_yes(api, session_id, plans()[-1])

    # Planned again is the answer to the failed check (flow step 9): the old row waits no
    # more, so it can't hold back the scene's bring-back or its plan's ad.
    [old] = first.scenes.all()
    assert old.landed_at is None
    assert old.error == "Scene 1 was planned again before its failed fix check was answered."
    assert run_plan.held_on_a_check(Session.objects.get()) == []


@pytest.mark.usefixtures("no_fix_redraws")
def test_go_ahead_carries_on_only_the_scenes_it_names_when_two_checks_wait(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    # A plan stored before the fixing agent: a new picture of B-roll scene 2 beside scene 1's.
    note = "Make the workshop brighter in scenes 1 and 2."
    both = [plan_row(number, "new_picture", change=BRIGHTER) for number in (1, 2)]
    tap_yes(api, session_id, stored(*both, note=note))
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)
    fake_model.respond("check_fix", *[not_made(HANDLE_HIDDEN)] * 4)
    held.run_held()
    assert list(FixCheck.objects.values_list("verdict", flat=True)) == ["failed", "failed"]
    # A go ahead naming no scene, with two checks waiting, is refused: it names one.
    fake_model.respond(
        "decide_fixes",
        turn(calls=[sort("go_ahead")]),
        turn(calls=[("sort_message", {"kind": "go_ahead", "scenes": [1]})]),
    )

    say("Scene 1 is fine, go ahead with it.")

    assert (
        ToolCall.objects.filter(agent="decider", tool="sort_message", result__startswith="Refused:")
        .get()
        .result.startswith("Refused: fix checks wait on scenes 1 and 2: name the scenes")
    )
    gone = dict(FixCheck.objects.values_list("step__scene__number", "gone_ahead_at"))
    assert gone[1] is not None and gone[2] is None
    [step] = SceneStep.objects.filter(pk__in=held.held)
    assert (step.kind, step.scene.number) == ("clip", 1)


def test_a_typed_yes_sent_before_the_plan_was_shown_runs_nothing(
    fake_model: FakeModel, held: HeldSteps, say: Callable[..., None]
) -> None:
    def typed_yes() -> None:
        Message.objects.create(
            session=Session.objects.get(),
            seq=Message.objects.order_by("seq").last().seq + 1,  # type: ignore[union-attr]
            role="user",
            text="yes",
        )

    # The shop owner types yes while the decider is still planning their note.
    fake_model.respond(
        "decide_fixes",
        meanwhile(
            typed_yes,
            planning(("look_at_picture", {"scene": 1}), decided(row(1, "new_picture", BRIGHTER))),
        ),
        turn(calls=[sort("yes")]),
    )
    fake_model.respond("produce", turn(says="Here's the plan: tap Yes if it looks right."))

    say("Scene 1 looks dark. Make the workshop brighter.")

    # It came before the notice, so it can't be a yes to it: the producer answers it.
    [plan] = plans()
    assert plan.state == "proposed"
    assert held.held == []
    assert Message.objects.filter(role="agent").last().text == (  # type: ignore[union-attr]
        "Here's the plan: tap Yes if it looks right."
    )


def test_a_new_picture_keeps_the_movement_of_the_clip_it_replaces(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    # The live ad's fix 1: a picture note gave the clip a newly chosen action the shop owner
    # never asked for. Only a new clip chooses a new movement (design thread, 05:15 Oct 17).
    # A plan stored before the fixing agent: a new picture of a B-roll scene.
    table = plan_row(2, "new_picture", change="The mug is on a wooden table.")
    tap_yes(api, session_id, stored(table, note="In scene 2 put the mug on a wooden table."))
    fake_model.respond("choose_broll_picture", {**BROLL_CHOICE, "motion_prompt": LIFT})

    held.run_held()

    assert status(2) == "finished"
    asked = fake_model.broll_clips_asked[-1]["prompt"]
    assert MOTION in asked
    assert LIFT not in asked


def test_a_movement_note_with_a_photo_of_a_thing_in_the_picture_moves_as_the_note_says(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    # A new clip whose photo's thing is in the picture is made a new picture, as the route
    # says; its clip still chooses its movement from the note (design thread, 05:15 Oct 17).
    # A plan stored before the fixing agent: a new clip of a B-roll scene, with the photo.
    note = "In scene 2 the hand should lift my black mug to the camera."
    mug = {**mug_photo_sent(fake_model, say, note), "in_picture": True}
    tap_yes(api, session_id, stored(plan_row(2, "new_clip", change=LIFT, photos=[mug]), note=note))
    fake_model.respond("choose_broll_picture", {**BROLL_CHOICE, "motion_prompt": LIFT})

    held.run_held()

    assert status(2) == "finished"
    asked = fake_model.broll_clips_asked[-1]["prompt"]
    assert LIFT in asked
    assert MOTION not in asked


def test_a_photos_thing_too_long_to_keep_is_refused_to_the_decider_which_names_it_again(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    # A name longer than the reference photo keeps would fail the yes that uses it.
    uses = [{"photo": 1, "in_picture": False, "during_action": True}]
    pours = row(2, "fix", "The hand pours tea into the black mug.", photos=uses)
    fake_model.respond(
        "decide_fixes",
        planning(
            ("look_at_owner_photo", {"photo": 1}),
            ("look_at_clip", {"scene": 2, "version": None}),
            decided(pours, photos=[{"photo": 1, "thing": "black mug " * 25, "clear": True}]),
        ),
        turn(calls=[decided(pours, photos=[{"photo": 1, "thing": "black mug", "clear": True}])]),
    )
    say("Pour the tea into my black mug in scene 2.", ("mug.png", MUG_SIDE))

    tap_yes(api, session_id, plans()[-1])

    [used] = ReferencePhoto.objects.filter(scene__number=2)
    assert used.thing == "black mug"


def test_a_decider_error_leaves_the_message_to_the_producer_and_later_ones_route(
    fake_model: FakeModel, held: HeldSteps, say: Callable[..., None]
) -> None:
    # An error that comes back every time on the same message must not hold every later
    # message, taps too, behind it (review 08; design thread, 05:15 Oct 17).
    fake_model.respond("decide_fixes", RuntimeError("the decider broke"))
    fake_model.respond("produce", turn(says="Sorry, could you say that again?"))

    say("Make it pop.")

    assert Message.objects.filter(role="user").last().routed_at is not None  # type: ignore[union-attr]
    assert Message.objects.filter(role="agent").last().text == "Sorry, could you say that again?"  # type: ignore[union-attr]
    [red] = Message.objects.filter(role="notice", level="problem").order_by("-seq")[:1]
    assert "couldn't be read for a fix" in red.text
    assert any("couldn't be read for a fix" in each["text"] for each in Job.objects.get().warnings)

    plan = a_picture_note_planned(fake_model, say)
    assert Message.objects.filter(plan_event="shown", fix_plan=plan).exists()


def test_a_yes_that_fails_to_run_is_said_so_in_red_and_later_messages_route(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An error that comes back every time on the same yes must not hold every later message
    # behind it, as a decider error doesn't (design thread, 05:15 Oct 17).
    plan = a_picture_note_planned(fake_model, say)

    def broken(plan: FixPlan) -> None:
        raise RuntimeError("the run broke")

    monkeypatch.setattr(run_plan, "run_after_yes", broken)
    tap_yes(api, session_id, plan)

    plan.refresh_from_db()
    assert plan.state == "proposed"
    assert Message.objects.get(plan_event="yes").routed_at is not None
    [red] = Message.objects.filter(role="notice", level="problem").order_by("-seq")[:1]
    assert "couldn't be acted on" in red.text
    fake_model.respond("produce", turn(says="It costs $0.81."))

    say("How much does that cost?")

    assert Message.objects.filter(role="user").last().routed_at is not None  # type: ignore[union-attr]
    assert Message.objects.filter(role="agent").last().text == "It costs $0.81."  # type: ignore[union-attr]


def _a_picture_note_on_scene_2_said_yes_to(
    fake_model: FakeModel, say: Callable[..., None], api: APIClient, session_id: str
) -> None:
    # A plan stored before the fixing agent: a new picture of a B-roll scene.
    table = plan_row(2, "new_picture", change="The mug is on a wooden table.")
    tap_yes(api, session_id, stored(table, note="In scene 2 put the mug on a wooden table."))
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)


def test_a_new_picture_keeps_the_movement_of_the_agents_try_the_judge_passed(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The first fix's clip fails, and the fixing agent's first try passes: the next new
    # picture moves as that try does.
    _a_picture_note_on_scene_2_said_yes_to(fake_model, say, api, session_id)
    fake_model.respond("check_clip_motion", *[failing(FLOATS)] * 3)
    agent_films(monkeypatch, REMADE)
    held.run_held()
    counter = plan_row(2, "new_picture", change="The mug is on a marble counter.")
    tap_yes(api, session_id, stored(counter, note="In scene 2 put the mug on a marble counter."))
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)

    held.run_held()

    assert status(2) == "finished"
    assert REMADE in fake_model.broll_clips_asked[-1]["prompt"]


def test_a_fix_clip_the_judge_couldnt_check_is_kept_and_the_chat_says_so(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    _a_picture_note_on_scene_2_said_yes_to(fake_model, say, api, session_id)
    unusable = UnusableReply("no answer", input_tokens=1, output_tokens=1)
    fake_model.respond("check_clip_sides", *[unusable] * 6, CLIP_SIDES_OK)

    held.run_held()

    assert status(2) == "finished"
    told = [each.text for each in Message.objects.filter(role="notice", level="info")]
    assert "Scene 2's new clip couldn't be checked, so it is kept unchecked." in told


def test_a_fix_clip_the_agent_gave_up_on_shows_its_tries_not_the_producer(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _a_picture_note_on_scene_2_said_yes_to(fake_model, say, api, session_id)
    fake_model.respond("check_clip_motion", *[failing(FLOATS)] * 9)
    agent_films(monkeypatch, REMADE, REMADE_AGAIN)

    held.run_held()

    assert status(2) == "finished"
    # The chat shows every try to pick from, not a line saying one was kept.
    assert Message.objects.filter(role="notice", tries_of__isnull=False).count() == 1
    told = [each.text for each in Message.objects.filter(role="notice", level="info")]
    assert not any("though the judge found a fault" in text for text in told)
    # Not the producer's to tell: no system note carries it (decision 20).
    assert not any(FLOATS in text for text in SystemNote.objects.values_list("text", flat=True))
