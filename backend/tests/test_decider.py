"""The decider (fix redesign, decisions 3, 15-18, 21): the agent that reads the shop owner's
messages once the ad has a clip, looks at the pictures it needs with its tools, and answers
in one `decide`, which code records as a fix plan. Run on the real loop and database, with
the model faked: no paid call."""

import io
import subprocess
from collections.abc import Sequence
from typing import Any

import PIL.Image
import pytest
from django.core.files.storage import default_storage
from django.utils import timezone

from adforge import file_store
from agents import decider
from agents.models import ToolCall
from chat import messages
from chat.models import Attachment, Message, Session
from gateway.fake import FakeModel, turn
from gateway.types import Happened, Said, ToolUse
from jobs import fix_plans
from jobs.models import (
    AgentFix,
    AgentFixTry,
    FixPlan,
    FixPlanScene,
    Job,
    ProducedItem,
    Scene,
    SceneStep,
)

pytestmark = pytest.mark.django_db(transaction=True)


def _png(colour: tuple[int, int, int]) -> bytes:
    made = io.BytesIO()
    PIL.Image.new("RGB", (30, 40), colour).save(made, format="PNG")
    return made.getvalue()


def _saved(name: str, colour: tuple[int, int, int]) -> str:
    return file_store.save(name, _png(colour))


def _step(scene: Scene, kind: str, call: ToolCall, **fields: Any) -> SceneStep:
    return SceneStep.objects.create(
        scene=scene,
        kind=kind,
        status=SceneStep.Status.FINISHED,
        tool_call=call,
        line=scene.line,
        shows=scene.shows,
        **fields,
    )


def _version(scene: Scene, call: ToolCall, number: int, note: str = "") -> ProducedItem:
    """A starting picture made for `note` (blank for the first making), and its clip, judged
    as passed with two kept frames."""
    picture_step = _step(scene, SceneStep.Kind.STARTING_PICTURE, call, note=note)
    picture = ProducedItem.objects.create(
        job=scene.job,
        scene=scene,
        step=picture_step,
        kind=ProducedItem.Kind.STARTING_PICTURE,
        version=number,
        file=_saved(f"s{scene.number}_v{number}.png", (10 * number, 20, 30)),
        change=note,
    )
    clip_step = _step(
        scene,
        SceneStep.Kind.CLIP,
        call,
        picture=picture,
        picture_step=picture_step,
        motion_prompt=f"A hand scrubs the toilet, take {number}.",
    )
    # The clip speaks its line's audio, as code estimates a fix of it from.
    audio = ProducedItem.objects.create(
        job=scene.job,
        scene=scene,
        step=_step(scene, SceneStep.Kind.LINE_AUDIO, call),
        kind=ProducedItem.Kind.LINE_AUDIO,
        version=number,
        file=f"s{scene.number}_v{number}.mp3",
        seconds=4.0,
    )
    frames = [
        {"times": [0.0], "key": _saved(f"s{scene.number}_v{number}_f0.png", (1, 2, 3))},
        {"times": [0.2], "key": _saved(f"s{scene.number}_v{number}_f1.png", (4, 5, 6))},
    ]
    ProducedItem.objects.create(
        job=scene.job,
        scene=scene,
        step=clip_step,
        kind=ProducedItem.Kind.CLIP,
        version=number,
        file=f"s{scene.number}_v{number}.mp4",
        picture=picture,
        made_from=audio,
        ruling={"verdict": "pass", "checks": {}, "pictures": {"frames": frames, "sides": []}},
        change=note,
    )
    return picture


@pytest.fixture
def ad() -> Job:
    """A finished ad: scene 1 talks to camera, scene 2 shows the product scrubbing a toilet,
    first made, then made again for a black toilet."""
    session = Session.objects.create()
    job = Job.objects.create(session=session, product_url="https://shop.example/products/gel")
    call = ToolCall.objects.create(
        session=session, job=job, agent="producer", tool="make_clip", call_id="c0", arguments={}
    )
    talking = Scene.objects.create(job=job, number=1, line="Meet the gel.", overlay="$9.99")
    _version(talking, call, 1)
    scrubbing = Scene.objects.create(
        job=job,
        number=2,
        line="One wipe and it shines.",
        overlay="Shines in one wipe",
        shows="A hand scrubs a toilet bowl with the gel.",
        broll_kind=Scene.BrollKind.DOES_A_JOB,
        person_shown=Scene.PersonShown.NO_FACE,
        usage="Squeeze the gel on, scrub, rinse.",
        result="The bowl shines.",
    )
    _version(scrubbing, call, 1)
    _version(scrubbing, call, 2, note="black toilet")
    return job


def _chat(job: Job) -> Session:
    assert job.session is not None
    return job.session


def _owner_says(job: Job, text: str) -> Message:
    return messages.add(_chat(job), role=Message.Role.USER, text=text)


def _decided(scenes: list[dict[str, Any]], **fields: Any) -> dict[str, Any]:
    return {"scenes": scenes, "photos": [], "cants": [], "question": "", **fields}


def _row(scene: int, action: str, change: str = "", **fields: Any) -> dict[str, Any]:
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


def _results(tool: str) -> list[str]:
    return list(
        ToolCall.objects.filter(agent="decider", tool=tool)
        .order_by("id")
        .values_list("result", flat=True)
    )


def test_a_picture_note_is_planned_after_the_decider_looks_at_the_scene(
    fake_model: FakeModel, ad: Job
) -> None:
    note = _owner_says(ad, "In scene 2 the toilet should be white.")
    picture_note = _decided([_row(2, "fix", "The toilet is white.")])
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        # It decides before looking: refused, and it looks, then decides again.
        turn(calls=[("decide", picture_note)]),
        turn(calls=[("look_at_picture", {"scene": 2})]),
        turn(calls=[("decide", picture_note)]),
    )

    outcome = decider.read(ad, [note])

    assert isinstance(outcome, decider.Planned)
    first, second = _results("decide")
    assert first.startswith("Refused: look at scene 2 first")
    [row] = outcome.plan.scenes.all()
    assert (row.scene.number, row.action, row.change) == (
        2,
        FixPlanScene.Action.FIX,
        "The toilet is white.",
    )
    assert outcome.plan.state == FixPlan.State.PROPOSED
    assert outcome.plan.notes == [{"seq": note.seq, "text": note.text}]
    # The look showed the scene's current starting picture, labelled by code.
    [look] = ToolCall.objects.filter(agent="decider", tool="look_at_picture")
    assert [picture["label"] for picture in look.pictures] == [
        "Scene 2, starting picture, v2 black toilet (the current version)"
    ]
    assert (
        look.pictures[0]["key"]
        == ad.scenes.get(number=2)
        .produced.get(kind=ProducedItem.Kind.STARTING_PICTURE, version=2)
        .file
    )
    # The decider never speaks in the chat: the plan is shown by code.
    assert not _chat(ad).messages.filter(role=Message.Role.AGENT).exists()


def _given(fake_model: FakeModel, turn_number: int) -> list[Any]:
    return list(fake_model.turns_taken[turn_number].handoff.conversation)


def _uses(given: Sequence[Happened], tool: str) -> list[ToolUse]:
    """The calls of `tool` in a conversation the decider was given."""
    return [each for each in given if isinstance(each, ToolUse) and each.tool == tool]


def test_the_decider_is_told_the_ad_and_the_note_and_doesnt_look_at_the_judge_for_a_colour(
    fake_model: FakeModel, ad: Job
) -> None:
    note = _owner_says(ad, "In scene 2 the toilet should be white.")
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        turn(calls=[("look_at_picture", {"scene": 2})]),
        turn(calls=[("decide", _decided([_row(2, "fix", "The toilet is white.")]))]),
    )

    decider.read(ad, [note])

    the_ad, said, *_ = _given(fake_model, 0)
    assert "Scene 1: the person talks to camera." in the_ad.text
    # Each overlay is read right after the line it goes with.
    assert 'Line: "Meet the gel."\nOverlay: "$9.99"' in the_ad.text
    assert 'Line: "One wipe and it shines."\nOverlay: "Shines in one wipe"' in the_ad.text
    assert "How the product is used: Squeeze the gel on, scrub, rinse." in the_ad.text
    assert "The current clip's video prompt: A hand scrubs the toilet, take 2." in the_ad.text
    assert "Versions: v1 first version; v2 black toilet (the current version)" in the_ad.text
    assert said.text == "In scene 2 the toilet should be white."
    # The judge's findings are a tool of their own, said what they are for, and not given on
    # a note about a colour.
    findings = decider.JudgeFindings.spec()
    assert findings.name in fake_model.turns_taken[0].handoff.tools
    assert "about how things move" in " ".join(findings.description.split())
    assert not ToolCall.objects.filter(agent="decider", tool="judge_findings").exists()


def test_an_approved_owner_part_is_told_as_settled(fake_model: FakeModel, ad: Job) -> None:
    part = {"what": "the nozzle", "source": "invented", "looks": "A short white angled spout."}
    scene = ad.scenes.get(number=2)
    scene.owner_part = part
    scene.save()
    scene.steps.update(owner_part=part)
    scene.owner_part_approved_at = timezone.now()
    scene.owner_part_approved_picture = scene.produced.filter(kind="starting_picture").last()
    scene.save()
    note = _owner_says(ad, "In scene 2 the toilet should be white.")
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        turn(calls=[("look_at_picture", {"scene": 2})]),
        turn(calls=[("decide", _decided([_row(2, "new_picture", "The toilet is white.")]))]),
    )

    decider.read(ad, [note])

    the_ad, *_ = _given(fake_model, 0)
    assert (
        "Its owner part, which the shop owner said yes to: the nozzle. A short white angled "
        "spout." in the_ad.text
    )


def test_going_back_needs_a_look_at_that_version(fake_model: FakeModel, ad: Job) -> None:
    note = _owner_says(ad, "I liked the first one better in scene 2.")
    back = _decided([_row(2, "go_back", go_back_to=1)])
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        turn(calls=[("look_at_picture", {"scene": 2})]),
        turn(calls=[("decide", back)]),
        turn(calls=[("look_at_version", {"scene": 2, "version": 1})]),
        turn(calls=[("decide", back)]),
    )

    outcome = decider.read(ad, [note])

    assert isinstance(outcome, decider.Planned)
    assert _results("decide")[0].startswith("Refused: look at version 1 of scene 2 first")
    [look] = ToolCall.objects.filter(agent="decider", tool="look_at_version")
    assert [picture["label"] for picture in look.pictures] == [
        "Scene 2, starting picture, v1 first version"
    ]
    [row] = outcome.plan.scenes.all()
    assert (row.action, row.go_back_to) == (FixPlanScene.Action.GO_BACK, 1)


def _owner_sends_a_photo(job: Job, text: str) -> Message:
    return messages.add(
        _chat(job),
        role=Message.Role.USER,
        text=text,
        carrying=[
            messages.AttachedFile(
                kind=Attachment.Kind.PICTURE, file=_saved("owner_toilet.png", (0, 0, 0))
            )
        ],
    )


def test_a_photo_in_the_picture_needs_no_look_at_the_frames(fake_model: FakeModel, ad: Job) -> None:
    note = _owner_sends_a_photo(ad, "Use my toilet in scene 2.")
    uses = [{"photo": 1, "in_picture": True, "during_action": False}]
    decided = _decided(
        [_row(2, "fix", "The toilet is the shop owner's black toilet.", photos=uses)],
        photos=[{"photo": 1, "thing": "black toilet", "clear": True}],
    )
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        turn(calls=[("look_at_picture", {"scene": 2})]),
        # The photo is used before it was looked at: refused.
        turn(calls=[("decide", decided)]),
        turn(calls=[("look_at_owner_photo", {"photo": 1})]),
        turn(calls=[("decide", decided)]),
    )

    outcome = decider.read(ad, [note])

    assert isinstance(outcome, decider.Planned)
    assert _results("decide")[0].startswith("Refused: look at owner photo 1 first")
    the_ad = _given(fake_model, 0)[0]
    assert "Photos the shop owner sent this round: owner photo 1." in the_ad.text
    [photo] = ToolCall.objects.get(agent="decider", tool="look_at_owner_photo").pictures
    assert photo["label"] == "Owner photo 1"
    [row] = outcome.plan.scenes.all()
    assert row.photos == [
        {
            "photo": 1,
            "file": photo["key"],
            "thing": "black toilet",
            "in_picture": True,
            "during_action": False,
        }
    ]
    assert not ToolCall.objects.filter(agent="decider", tool="look_at_clip").exists()


def test_a_photos_thing_only_during_the_action_needs_a_look_at_the_clip(
    fake_model: FakeModel, ad: Job
) -> None:
    note = _owner_sends_a_photo(ad, "This is my gel, use it.")
    uses = [{"photo": 1, "in_picture": False, "during_action": True}]
    decided = _decided(
        [_row(2, "fix", "The gel squeezed on is the shop owner's blue gel.", photos=uses)],
        photos=[{"photo": 1, "thing": "blue gel", "clear": True}],
    )
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        turn(calls=[("look_at_owner_photo", {"photo": 1}), ("look_at_picture", {"scene": 2})]),
        turn(calls=[("decide", decided)]),
        turn(calls=[("look_at_clip", {"scene": 2, "version": None})]),
        turn(calls=[("decide", decided)]),
    )

    outcome = decider.read(ad, [note])

    assert isinstance(outcome, decider.Planned)
    assert _results("decide")[0].startswith("Refused: look at scene 2's clip first")
    look = ToolCall.objects.get(agent="decider", tool="look_at_clip")
    # The judge's kept frames, each labelled with its time, and a line naming the judge's tool.
    assert [picture["label"] for picture in look.pictures] == [
        "Scene 2, clip of v2 black toilet (the current version), Frame at 0 s",
        "Scene 2, clip of v2 black toilet (the current version), Frame at 0.2 s",
    ]
    clip = ad.scenes.get(number=2).produced.get(kind=ProducedItem.Kind.CLIP, version=2)
    assert [picture["key"] for picture in look.pictures] == [
        each["key"] for each in (clip.ruling or {})["pictures"]["frames"]
    ]
    assert "The judge passed this clip." in look.result
    assert "judge_findings" in look.result
    [row] = outcome.plan.scenes.all()
    assert row.photos[0]["during_action"] is True


def test_a_message_that_isnt_a_fix_note_is_said_so_without_a_look(
    fake_model: FakeModel, ad: Job
) -> None:
    question = _owner_says(ad, "What's the cost so far?")
    fake_model.respond("decide_fixes", turn(calls=[("sort_message", {"kind": "not_a_fix"})]))

    outcome = decider.read(ad, [question])

    assert outcome == decider.NotAFix()
    assert list(ToolCall.objects.filter(agent="decider").values_list("tool", flat=True)) == [
        "sort_message"
    ]
    assert not FixPlan.objects.exists()


def test_a_look_before_the_message_is_sorted_is_refused(fake_model: FakeModel, ad: Job) -> None:
    note = _owner_says(ad, "Scene 2 is too dark.")
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("look_at_picture", {"scene": 2})]),
        turn(calls=[("sort_message", {"kind": "not_a_fix"})]),
    )

    decider.read(ad, [note])

    assert _results("look_at_picture")[0].startswith(
        "Refused: sort the shop owner's newest message with sort_message first."
    )
    assert ToolCall.objects.get(tool="look_at_picture").pictures == []


@pytest.fixture
def planned(fake_model: FakeModel, ad: Job) -> tuple[Message, decider.Planned]:
    """A note on scenes 1 and 2, planned, waiting for the shop owner's answer."""
    note = _owner_says(ad, "Scene 2's toilet should be white, and scene 1 brighter.")
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        turn(calls=[("look_at_picture", {"scene": 2}), ("look_at_picture", {"scene": 1})]),
        turn(
            calls=[
                (
                    "decide",
                    _decided(
                        [
                            _row(1, "new_picture", "The scene is brighter."),
                            _row(2, "fix", "The toilet is white."),
                        ]
                    ),
                )
            ]
        ),
    )
    outcome = decider.read(ad, [note])
    assert isinstance(outcome, decider.Planned)
    return note, outcome


@pytest.mark.parametrize(
    ("answer", "kind", "outcome"),
    [
        ("yes", "yes", decider.Yes),
        ("no", "no", decider.No),
    ],
)
def test_a_typed_answer_to_the_plan_is_read_as_yes_or_no(
    fake_model: FakeModel,
    ad: Job,
    planned: tuple[Message, decider.Planned],
    answer: str,
    kind: str,
    outcome: type[decider.Yes | decider.No],
) -> None:
    note, made = planned
    said = _owner_says(ad, answer)
    fake_model.respond("decide_fixes", turn(calls=[("sort_message", {"kind": kind})]))

    read = decider.read(ad, [note, said])

    assert read == outcome(made.plan)


def test_wait_leaves_the_plan_waiting(
    fake_model: FakeModel, ad: Job, planned: tuple[Message, decider.Planned]
) -> None:
    note, made = planned
    said = _owner_says(ad, "wait")
    fake_model.respond("decide_fixes", turn(calls=[("sort_message", {"kind": "not_a_fix"})]))

    assert decider.read(ad, [note, said]) == decider.NotAFix()
    made.plan.refresh_from_db()
    assert made.plan.state == FixPlan.State.PROPOSED


def test_a_correction_plans_again_on_the_looks_already_made(
    fake_model: FakeModel, ad: Job, planned: tuple[Message, decider.Planned]
) -> None:
    note, made = planned
    correction = _owner_says(ad, "no, only scene 2")
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        turn(calls=[("decide", _decided([_row(2, "fix", "The toilet is white.")]))]),
    )
    looked_before = len(fake_model.turns_taken)

    outcome = decider.read(ad, [note, correction])

    assert isinstance(outcome, decider.Planned)
    # The round's context is kept: the first note, its looks and the plan, then the correction.
    given = _given(fake_model, looked_before)
    kinds = [(each.kind, getattr(each, "tool", None)) for each in given[1:]]
    assert kinds == [
        ("said", None),
        ("tool_use", "sort_message"),
        ("tool_use", "look_at_picture"),
        ("tool_use", "look_at_picture"),
        ("tool_use", "decide"),
        ("said", None),
    ]
    assert given[-1].text == "no, only scene 2"
    assert [row.scene.number for row in outcome.plan.scenes.all()] == [2]
    assert outcome.plan.notes == [
        {"seq": note.seq, "text": note.text},
        {"seq": correction.seq, "text": correction.text},
    ]
    made.plan.refresh_from_db()
    assert made.plan.state == FixPlan.State.REPLACED


def test_yes_with_no_plan_waiting_is_refused(fake_model: FakeModel, ad: Job) -> None:
    said = _owner_says(ad, "yes")
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "yes"})]),
        turn(calls=[("sort_message", {"kind": "not_a_fix"})]),
    )

    assert decider.read(ad, [said]) == decider.NotAFix()
    assert _results("sort_message")[0].startswith(
        "Refused: no plan is waiting for the shop owner's answer."
    )


def test_a_failed_clips_findings_say_what_the_judge_found(fake_model: FakeModel, ad: Job) -> None:
    clip = ad.scenes.get(number=2).produced.get(kind=ProducedItem.Kind.CLIP, version=2)
    clip.ruling = {
        **(clip.ruling or {}),
        "verdict": "fail",
        "checks": {"sides": "fail"},
        "answers": {
            "sides": [
                {"verdict": "fail", "problem": "The bottle flips to its back at 1.2 s."},
                {"verdict": "fail", "problem": "The bottle turns round at 1.4 s."},
                {"verdict": "pass", "problem": None},
            ],
            "motion": [],
        },
        "let_go": [
            {
                "check": "sides",
                "ask": 3,
                "problem": "A drop of gel blurs at 0.4 s.",
                "back_to_normal_at": 0.5,
            }
        ],
    }
    clip.save()
    note = _owner_says(ad, "The bottle flips in scene 2.")
    flips = _decided([_row(2, "fix", "The bottle never turns.")])
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        turn(calls=[("judge_findings", {"scene": 2, "version": None})]),
        # The judge's words aren't a look at the scene: it can be wrong (decision 16).
        turn(calls=[("decide", flips)]),
        turn(calls=[("look_at_clip", {"scene": 2, "version": None})]),
        turn(calls=[("decide", flips)]),
    )

    outcome = decider.read(ad, [note])

    [found] = _results("judge_findings")
    assert found == (
        "Scene 2, clip of v2 black toilet: The judge failed this clip. "
        "It found: The bottle flips to its back at 1.2 s. "
        "It found: The bottle turns round at 1.4 s. "
        "It let go, as back to normal soon: A drop of gel blurs at 0.4 s."
    )
    assert _results("decide")[0].startswith("Refused: look at scene 2 first")
    assert isinstance(outcome, decider.Planned)


def test_a_worker_that_stopped_carries_on_from_the_checkpoints(
    fake_model: FakeModel, ad: Job, settings: Any
) -> None:
    settings.RETRY_DELAYS_SECONDS = []
    note = _owner_says(ad, "In scene 2 the toilet should be white.")
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        turn(calls=[("look_at_picture", {"scene": 2})]),
        RuntimeError("the worker stopped"),
    )
    with pytest.raises(RuntimeError):
        decider.read(ad, [note])
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("decide", _decided([_row(2, "fix", "The toilet is white.")]))]),
    )

    outcome = decider.read(ad, [note])

    assert isinstance(outcome, decider.Planned)
    stopped, restarted = fake_model.turns_taken[2:]
    assert restarted.handoff == stopped.handoff
    assert [image.key for image in restarted.images] == [image.key for image in stopped.images]
    assert ToolCall.objects.filter(agent="decider", tool="look_at_picture").count() == 1


def test_a_decider_that_answers_without_a_tool_is_reminded_then_stopped(
    fake_model: FakeModel, ad: Job
) -> None:
    note = _owner_says(ad, "Scene 2 is too dark.")
    fake_model.respond(
        "decide_fixes",
        turn("I think it should be brighter."),
        turn(calls=[("sort_message", {"kind": "not_a_fix"})]),
    )
    assert decider.read(ad, [note]) == decider.NotAFix()
    reminder = fake_model.turns_taken[1].handoff.conversation[-1]
    assert isinstance(reminder, Said) and reminder.text == decider.REMINDER

    again = _owner_says(ad, "Scene 2 is still too dark.")
    fake_model.respond("decide_fixes", turn("Hmm."), turn("Hmm."), turn("Hmm."))
    with pytest.raises(decider.NoDecision):
        decider.read(ad, [note, again])
    assert not _chat(ad).messages.filter(role=Message.Role.AGENT).exists()


def test_a_round_is_the_owners_messages_since_their_last_yes_once_the_ad_had_a_clip(
    ad: Job,
) -> None:
    session = _chat(ad)
    first_clip = ad.produced.filter(kind=ProducedItem.Kind.CLIP).order_by("created_at").first()
    assert first_clip is not None
    before = _owner_says(ad, "Make me an ad.")
    Message.objects.filter(pk=before.pk).update(created_at=first_clip.created_at)
    note = _owner_says(ad, "Scene 2 is too dark.")
    assert decider.this_round(ad) == [note]

    yes = _owner_says(ad, "yes")
    FixPlan.objects.create(job=ad, session=session, state=FixPlan.State.CONFIRMED, confirmed_by=yes)
    after = _owner_says(ad, "Now scene 1.")
    assert decider.this_round(ad) == [after]


def test_a_new_round_starts_with_none_of_the_last_rounds_looks(
    fake_model: FakeModel, ad: Job, planned: tuple[Message, decider.Planned]
) -> None:
    _, made = planned
    yes = _owner_says(ad, "yes")
    made.plan.state = FixPlan.State.CONFIRMED
    made.plan.confirmed_by = yes
    made.plan.save()
    _owner_says(ad, "Scene 1 is too dark now.")
    fake_model.respond("decide_fixes", turn(calls=[("sort_message", {"kind": "not_a_fix"})]))
    taken = len(fake_model.turns_taken)

    decider.read(ad, decider.this_round(ad))

    given = _given(fake_model, taken)
    assert [each.kind for each in given] == ["said", "said"]
    assert given[1].text == "Scene 1 is too dark now."


def test_a_note_that_cant_be_done_is_planned_with_its_cant_said_by_code(
    fake_model: FakeModel, ad: Job
) -> None:
    note = _owner_says(ad, "Make scene 2 slower.")
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        turn(calls=[("look_at_clip", {"scene": 2, "version": None})]),
        turn(calls=[("decide", _decided([_row(2, "no_change", cant="slower")]))]),
    )

    outcome = decider.read(ad, [note])

    assert isinstance(outcome, decider.Planned)
    [row] = outcome.plan.scenes.all()
    assert (row.action, row.cant) == (
        FixPlanScene.Action.NO_CHANGE,
        "Slower can't be done: every clip plays at real-time speed.",
    )


def test_the_reason_for_no_change_reaches_the_plan_notice(fake_model: FakeModel, ad: Job) -> None:
    """The owner corrects which scene they meant (replay-8-correction): each no change's reason
    is kept on its row and said in the plan notice. The rule that it needs one is pinned by the
    refusal in test_a_decide_that_breaks_a_rule_is_refused_and_nothing_is_planned."""
    note = _owner_says(ad, "Sorry, I meant scene 2, not scene 1.")
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        turn(calls=[("look_at_clip", {"scene": 1, "version": None})]),
        turn(calls=[("look_at_clip", {"scene": 2, "version": None})]),
        turn(
            calls=[
                (
                    "decide",
                    _decided(
                        [
                            _row(1, "no_change", "You meant scene 2, so scene 1 stays."),
                            _row(2, "no_change", "Scene 2's brush already stays inside the bowl."),
                        ]
                    ),
                )
            ]
        ),
    )

    outcome = decider.read(ad, [note])

    assert isinstance(outcome, decider.Planned)
    shown = fix_plans.notice(outcome.plan)
    assert [scene["change"] for scene in shown["scenes"]] == [
        "You meant scene 2, so scene 1 stays.",
        "Scene 2's brush already stays inside the bowl.",
    ]
    said = fix_plans.show(outcome.plan)
    assert said is not None
    assert "Scene 2, no change: Scene 2's brush already stays inside the bowl." in said.text


def test_an_action_while_talking_is_a_cant_and_what_they_hold_is_a_new_picture(
    fake_model: FakeModel, ad: Job
) -> None:
    note = _owner_says(ad, "In scene 1 she should hold the gel up and point at it.")
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        turn(calls=[("look_at_picture", {"scene": 1})]),
        turn(
            calls=[
                (
                    "decide",
                    _decided(
                        [_row(1, "new_picture", "She holds the gel up.", cant="talking_action")]
                    ),
                )
            ]
        ),
    )

    outcome = decider.read(ad, [note])

    assert isinstance(outcome, decider.Planned)
    [row] = outcome.plan.scenes.all()
    assert (row.action, row.cant) == (
        FixPlanScene.Action.NEW_PICTURE,
        "A person can't do an action while talking yet.",
    )
    # The decider is told the talking scene's clip only makes the person speak.
    assert "what they hold, wear or stand next to is a new picture" in " ".join(
        decider.INSTRUCTIONS.split()
    )


def test_a_new_line_keeps_the_picture_and_one_with_a_visual_change_is_a_new_picture_with_it(
    fake_model: FakeModel, ad: Job
) -> None:
    # A new line is estimated as long as the person's voice takes to say it.
    ProducedItem.objects.create(
        job=ad, kind=ProducedItem.Kind.VOICE, version=1, file="voice.mp3", words_per_second=2.0
    )
    note = _owner_says(ad, 'Scene 1 should say "Shines in one wipe." and she should hold the gel.')
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        turn(calls=[("look_at_picture", {"scene": 1})]),
        turn(
            calls=[
                (
                    "decide",
                    _decided(
                        [
                            _row(
                                1,
                                "new_picture",
                                "She holds the gel.",
                                line="Shines in one wipe.",
                                overlay="One wipe",
                            )
                        ]
                    ),
                )
            ]
        ),
    )

    outcome = decider.read(ad, [note])

    assert isinstance(outcome, decider.Planned)
    [row] = outcome.plan.scenes.all()
    assert (row.action, row.change, row.line, row.overlay) == (
        FixPlanScene.Action.NEW_PICTURE,
        "She holds the gel.",
        "Shines in one wipe.",
        "One wipe",
    )
    # The decider is told a new line keeps the picture unless the owner asks for a visual change.
    told = " ".join(decider.INSTRUCTIONS.split())
    assert "A new line keeps the scene's starting picture" in told
    assert "a new picture with the new line" in told
    assert "its change says only what must look different on screen" in told


def test_a_photos_thing_found_in_no_scene_is_asked_about(fake_model: FakeModel, ad: Job) -> None:
    note = _owner_sends_a_photo(ad, "Here's my sponge, use it.")
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        turn(calls=[("look_at_owner_photo", {"photo": 1})]),
        # Neither a scene nor a question: refused.
        turn(
            calls=[
                ("decide", _decided([], photos=[{"photo": 1, "thing": "sponge", "clear": True}]))
            ]
        ),
        turn(
            calls=[
                (
                    "decide",
                    _decided(
                        [],
                        photos=[{"photo": 1, "thing": "sponge", "clear": True}],
                        question=" Which scene should show your sponge? ",
                    ),
                )
            ]
        ),
    )

    outcome = decider.read(ad, [note])

    assert isinstance(outcome, decider.Planned)
    assert _results("decide")[0].startswith("Refused: decide at least one scene")
    assert outcome.plan.question == "Which scene should show your sponge?"
    assert not outcome.plan.scenes.exists()


@pytest.mark.real_ffmpeg
def test_a_clip_the_judge_never_saw_is_cut_into_frames_to_look_at(
    fake_model: FakeModel, ad: Job, tmp_path: Any
) -> None:
    made = tmp_path / "talking.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=64x96:rate=25"]
        + ["-t", "1", "-pix_fmt", "yuv420p", str(made)],
        check=True,
    )
    clip = ad.scenes.get(number=1).produced.get(kind=ProducedItem.Kind.CLIP)
    clip.file = file_store.save("talking.mp4", made.read_bytes())
    clip.ruling = None
    clip.save()
    note = _owner_says(ad, "In scene 1 she should hold the bottle higher.")
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        turn(calls=[("look_at_clip", {"scene": 1, "version": None})]),
        turn(calls=[("sort_message", {"kind": "not_a_fix"})]),
    )

    decider.read(ad, [note])

    look = ToolCall.objects.get(agent="decider", tool="look_at_clip")
    assert [picture["label"].rsplit(", ", 1)[1] for picture in look.pictures] == [
        "Frame at 0 s",
        "Frame at 0.2 s",
        "Frame at 0.4 s",
        "Frame at 0.6 s",
        "Frame at 0.8 s",
    ]
    assert "The judge didn't judge this clip." in look.result


def test_a_cant_not_about_one_scene_is_on_the_plan_with_no_look(
    fake_model: FakeModel, ad: Job
) -> None:
    note = _owner_says(ad, "Can you change the music?")
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        turn(calls=[("decide", _decided([], cants=["music"]))]),
    )

    outcome = decider.read(ad, [note])

    assert isinstance(outcome, decider.Planned)
    assert outcome.plan.cants == [
        {"kind": FixPlan.Cant.MUSIC, "text": "Changing the music can't be done yet."}
    ]
    assert not outcome.plan.scenes.exists()


def test_a_photo_in_the_picture_and_during_the_action_needs_no_look_at_the_frames(
    fake_model: FakeModel, ad: Job
) -> None:
    note = _owner_sends_a_photo(ad, "Use my gel jar in scene 2.")
    uses = [{"photo": 1, "in_picture": True, "during_action": True}]
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        turn(calls=[("look_at_owner_photo", {"photo": 1}), ("look_at_picture", {"scene": 2})]),
        turn(
            calls=[
                (
                    "decide",
                    _decided(
                        [_row(2, "fix", "The jar is the shop owner's jar.", photos=uses)],
                        photos=[{"photo": 1, "thing": "gel jar", "clear": False}],
                    ),
                )
            ]
        ),
    )

    outcome = decider.read(ad, [note])

    assert isinstance(outcome, decider.Planned)
    [row] = outcome.plan.scenes.all()
    assert [(each["in_picture"], each["during_action"]) for each in row.photos] == [(True, True)]
    # Code turned the photo that isn't clear into the can't that asks for a clearer one.
    assert "clearly enough" in row.cant


@pytest.mark.parametrize(
    ("decided", "refused"),
    [
        (_decided([_row(2, "fix", "Faster.")]), "look at scene 2 first."),
        (
            _decided(
                [_row(1, "new_picture", "Brighter."), _row(1, "new_line", "New.", line="Hi.")]
            ),
            "scene 1 is decided twice.",
        ),
        (
            _decided([_row(2, "fix", "Faster.", line="A line nobody asked for.")]),
            "it was called with arguments it can't use (scenes.0: scene 2 has a line, but only a "
            "new line or a new picture takes one).",
        ),
        (
            _decided(
                [
                    _row(
                        1,
                        "new_picture",
                        "Holds the owner's cup.",
                        photos=[{"photo": 1, "in_picture": True, "during_action": False}],
                    )
                ]
            ),
            "scene 1 uses photo 1, which isn't in photos with its thing.",
        ),
        (
            _decided([_row(1, "new_picture", "Brighter.", overlay="$8.99")]),
            "it was called with arguments it can't use (scenes.0: scene 1 has an overlay, but "
            "no new line to go with it).",
        ),
        (
            _decided(
                [
                    _row(
                        1,
                        "new_line",
                        "New price.",
                        line="Now $8.99.",
                        overlay="Now only $8.99 " * 3,
                    )
                ]
            ),
            "it was called with arguments it can't use (scenes.0: scene 1's overlay is a few "
            "words: 30 characters at most).",
        ),
        (
            _decided([_row(1, "fix", "She holds the gel up.")]),
            "scene 1 shows the person talking to camera, which the fixing agent doesn't film: "
            "change it with a new picture or a new line.",
        ),
        (
            _decided([_row(2, "new_picture", "The toilet is white.")]),
            "scene 2 is a B-roll scene: its change is a fix, which the fixing agent makes.",
        ),
        (
            _decided([_row(2, "fix", "")]),
            "it was called with arguments it can't use (scenes.0: scene 2 needs the change in "
            "plain words).",
        ),
        (
            _decided([_row(1, "no_change")]),
            "it was called with arguments it can't use (scenes.0: scene 1 is no change, so it "
            "needs the reason in one line).",
        ),
    ],
)
def test_a_decide_that_breaks_a_rule_is_refused_and_nothing_is_planned(
    fake_model: FakeModel, ad: Job, decided: dict[str, Any], refused: str
) -> None:
    note = _owner_sends_a_photo(ad, "Scene 1 should hold my cup.")
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        turn(calls=[("look_at_picture", {"scene": 1}), ("look_at_owner_photo", {"photo": 1})]),
        turn(calls=[("decide", decided)]),
        turn(calls=[("sort_message", {"kind": "not_a_fix"})]),
    )

    decider.read(ad, [note])

    assert _results("decide") == [f"Refused: {refused} Nothing was done."]
    assert not FixPlan.objects.exists()


def test_a_clip_made_again_from_the_same_picture_is_a_version_of_its_own(
    fake_model: FakeModel, ad: Job
) -> None:
    scene = ad.scenes.get(number=2)
    picture = scene.produced.get(kind=ProducedItem.Kind.STARTING_PICTURE, version=2)
    call = ToolCall.objects.get(tool="make_clip")
    # A clip step for a note, then the fixing agent's try of its clip, in a step of its own.
    step = _step(scene, SceneStep.Kind.CLIP, call, picture=picture, note="scrub faster")
    fixing = AgentFix.objects.create(step=step, door=AgentFix.Door.JUDGE)
    agents_call = ToolCall.objects.create(
        session=call.session,
        job=ad,
        agent="fixing_agent",
        agent_fix=fixing,
        tool="film_again",
        arguments={},
    )
    tried = _step(scene, SceneStep.Kind.CLIP, agents_call, picture=picture, note="scrub faster")
    rulings = [{"verdict": "fail", "failed_answers": 1, "checks": {}}, {"verdict": "pass"}]
    for at, version, remake, ruling in [(step, 3, None, rulings[0]), (tried, 4, 1, rulings[1])]:
        made = ProducedItem.objects.create(
            job=ad,
            scene=scene,
            step=at,
            kind=ProducedItem.Kind.CLIP,
            version=version,
            file=f"remade_{version}.mp4",
            picture=picture,
            remake=remake,
            change="scrub faster",
            ruling=ruling,
        )
    AgentFixTry.objects.create(
        agent_fix=fixing, number=1, tool_call=agents_call, step=tried, picture=picture, clip=made
    )
    note_message = _owner_says(ad, "Scene 2 is too dark.")
    fake_model.respond("decide_fixes", turn(calls=[("sort_message", {"kind": "not_a_fix"})]))

    decider.read(ad, [note_message])

    the_ad = _given(fake_model, 0)[0]
    assert (
        "Versions: v1 first version; v2 black toilet; v3 scrub faster (first clip); v4 scrub "
        "faster (try 1) (the current version)" in the_ad.text
    )


def test_the_decider_numbers_versions_as_going_back_does(fake_model: FakeModel, ad: Job) -> None:
    """The decider sees the versions code goes back to: a picture going back brought back is
    named so, and a picture whose step is still running isn't one yet."""
    scene = ad.scenes.get(number=2)
    first = scene.produced.get(kind=ProducedItem.Kind.STARTING_PICTURE, version=1)
    ProducedItem.objects.create(
        job=ad,
        scene=scene,
        step=first.step,
        kind=ProducedItem.Kind.STARTING_PICTURE,
        version=3,
        file=first.file,
        change="back to v1",
        back_to=1,
    )
    call = ToolCall.objects.get(tool="make_clip")
    running = _step(scene, SceneStep.Kind.STARTING_PICTURE, call, note="blue gel")
    running.status = SceneStep.Status.RUNNING
    running.save()
    ProducedItem.objects.create(
        job=ad,
        scene=scene,
        step=running,
        kind=ProducedItem.Kind.STARTING_PICTURE,
        version=4,
        file="drawing.png",
        change="blue gel",
    )
    note_message = _owner_says(ad, "Scene 2 is too dark.")
    fake_model.respond("decide_fixes", turn(calls=[("sort_message", {"kind": "not_a_fix"})]))

    decider.read(ad, [note_message])

    the_ad = _given(fake_model, 0)[0]
    assert (
        "Versions: v1 first version; v2 black toilet; v3 back to v1 (the current version)"
        in the_ad.text
    )


def test_an_ad_with_no_clip_yet_has_no_round(ad: Job) -> None:
    ProducedItem.objects.filter(job=ad, kind=ProducedItem.Kind.CLIP).delete()
    _owner_says(ad, "Make it brighter.")

    assert decider.this_round(ad) == []


def test_a_sort_kept_after_a_newer_message_came_in_doesnt_answer_the_newer_one(
    fake_model: FakeModel, ad: Job
) -> None:
    question = _owner_says(ad, "What's the cost so far?")
    # The note comes in while the decider is still sorting the question.
    note = _owner_says(ad, "In scene 2 the toilet should be white.")
    fake_model.respond("decide_fixes", turn(calls=[("sort_message", {"kind": "not_a_fix"})]))
    assert decider.read(ad, [question]) == decider.NotAFix()
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        turn(calls=[("look_at_picture", {"scene": 2})]),
        turn(calls=[("decide", _decided([_row(2, "fix", "The toilet is white.")]))]),
    )

    outcome = decider.read(ad, [question, note])

    assert isinstance(outcome, decider.Planned)
    assert _results("sort_message") == [
        f"Sorted message {question.seq} as not_a_fix. You are done.",
        f"Sorted message {note.seq} as fix_note. Look at what you need, then decide.",
    ]


def test_a_decide_after_a_yes_is_refused_and_the_plan_shown_stays(
    fake_model: FakeModel, ad: Job, planned: tuple[Message, decider.Planned]
) -> None:
    note, made = planned
    said = _owner_says(ad, "yes")
    fake_model.respond(
        "decide_fixes",
        turn(
            calls=[
                ("sort_message", {"kind": "yes"}),
                ("decide", _decided([_row(2, "fix", "The toilet is red.")])),
            ]
        ),
    )

    assert decider.read(ad, [note, said]) == decider.Yes(made.plan)

    assert _results("decide")[-1].startswith(
        "Refused: the shop owner's newest message is sorted as yes, so there is nothing to decide."
    )
    made.plan.refresh_from_db()
    assert made.plan.state == FixPlan.State.PROPOSED
    assert FixPlan.objects.count() == 1


def test_a_decide_run_again_after_a_restart_records_its_plan_once(
    fake_model: FakeModel, ad: Job, planned: tuple[Message, decider.Planned]
) -> None:
    note, made = planned
    # The worker stopped after the plan was recorded, before the call was kept as done.
    ToolCall.objects.filter(agent="decider", tool="decide").update(result="", finished_at=None)
    taken = len(fake_model.turns_taken)

    assert decider.read(ad, [note]) == made

    assert len(fake_model.turns_taken) == taken
    assert FixPlan.objects.get() == made.plan
    assert FixPlan.objects.get().state == FixPlan.State.PROPOSED


def test_a_message_is_sorted_without_the_pictures_already_looked_at(
    fake_model: FakeModel, ad: Job, planned: tuple[Message, decider.Planned]
) -> None:
    note, _ = planned
    question = _owner_says(ad, "What's the cost so far?")
    fake_model.respond("decide_fixes", turn(calls=[("sort_message", {"kind": "not_a_fix"})]))

    assert decider.read(ad, [note, question]) == decider.NotAFix()

    sorting = fake_model.turns_taken[-1]
    assert sorting.images == ()
    looks = _uses(sorting.handoff.conversation, "look_at_picture")
    assert [look.result for look in looks] == [
        "Scene 2, starting picture, v2 black toilet (the current version).",
        "Scene 1, starting picture, v1 first version (the current version).",
    ]


def test_each_look_is_shown_once_and_the_oldest_go_past_the_most_pictures(
    fake_model: FakeModel, ad: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(decider, "MOST_PICTURES", 1)
    note = _owner_says(ad, "Scene 2 should be brighter.")
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        turn(calls=[("look_at_picture", {"scene": 2})]),
        turn(calls=[("look_at_picture", {"scene": 2})]),
        turn(calls=[("look_at_clip", {"scene": 2, "version": None})]),
        turn(calls=[("decide", _decided([_row(2, "fix", "The scene is brighter.")]))]),
    )

    decider.read(ad, [note])

    again, clip = fake_model.turns_taken[3:5]
    picture = ad.scenes.get(number=2).produced.get(
        kind=ProducedItem.Kind.STARTING_PICTURE, version=2
    )
    # Looked at twice: shown once, with the later look.
    assert [image.key for image in again.images] == [picture.file]
    first_look = _uses(again.handoff.conversation, "look_at_picture")[0]
    assert first_look.result.endswith("(Its pictures are shown with the same look, later.)")
    # The clip's two frames, joined into one picture, fill the turn: with nothing left to
    # join, the picture is past the most and said so.
    assert [image.label for image in clip.images] == [
        "Scene 2, clip of v2 black toilet (the current version), "
        "Frames at 0 s, 0.2 s, left to right"
    ]
    looks = _uses(clip.handoff.conversation, "look_at_picture")
    assert looks[-1].result.endswith(
        "(Its pictures aren't shown any more: look again to see them.)"
    )


def test_two_clips_past_the_most_pictures_are_both_shown_with_their_frames_side_by_side(
    fake_model: FakeModel, ad: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A note that needs two clips' frames (the gel gap, decision 9): the decider once
    looked at one clip, was told to look again at the other, and so on to the limit of
    tools (07's rule case gap-foam-no-scene). Past the most pictures, every frame stays, two
    or more to a picture, as the clip judge shows them, so every look is seen at once."""
    monkeypatch.setattr(decider, "MOST_PICTURES", 3)
    note = _owner_says(ad, "Our gel foams up blue when you scrub.")
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        turn(calls=[("look_at_picture", {"scene": 2})]),
        turn(calls=[("look_at_clip", {"scene": 2, "version": 1})]),
        turn(calls=[("look_at_clip", {"scene": 2, "version": 2})]),
        turn(calls=[("decide", _decided([_row(2, "fix", "The gel foams up blue.")]))]),
    )

    outcome = decider.read(ad, [note])

    assert isinstance(outcome, decider.Planned)
    both = fake_model.turns_taken[4]
    picture = ad.scenes.get(number=2).produced.get(
        kind=ProducedItem.Kind.STARTING_PICTURE, version=2
    )
    assert [image.label for image in both.images] == [
        "Scene 2, starting picture, v2 black toilet (the current version)",
        "Scene 2, clip of v1 first version, Frames at 0 s, 0.2 s, left to right",
        "Scene 2, clip of v2 black toilet (the current version), "
        "Frames at 0 s, 0.2 s, left to right",
    ]
    assert both.images[0].key == picture.file
    joined = PIL.Image.open(io.BytesIO(file_store.read(both.images[2].key)))
    assert joined.size == (60, 40)
    assert "look again" not in " ".join(
        use.result for use in both.handoff.conversation if isinstance(use, ToolUse)
    )
    # Joined once: a later turn shows the same pictures, so a restart replays it for free.
    again = decider._conversation(decider._Round(ad, (note,)))
    shown_again = [p.key for use in again if isinstance(use, ToolUse) for p in use.pictures]
    assert sorted(shown_again) == sorted(image.key for image in both.images)
    # One a stopped worker left half-saved is made again under the same name, not shown cut.
    whole = file_store.read(both.images[2].key)
    default_storage.delete(both.images[2].key)
    assert file_store.save(both.images[2].key, whole[: len(whole) // 3]) == both.images[2].key
    again = decider._conversation(decider._Round(ad, (note,)))
    shown_again = [p.key for use in again if isinstance(use, ToolUse) for p in use.pictures]
    assert sorted(shown_again) == sorted(image.key for image in both.images)
    assert file_store.read(both.images[2].key) == whole


def test_a_long_clips_frames_already_in_pairs_are_joined_with_every_time(
    fake_model: FakeModel, ad: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clip of 10 s or more comes from the judge with its frames already two to a
    picture; past the most pictures those pictures are joined again, labelled with every
    frame's time."""
    monkeypatch.setattr(decider, "MOST_PICTURES", 1)
    clip = ad.scenes.get(number=2).produced.get(kind=ProducedItem.Kind.CLIP, version=2)
    clip.ruling = {
        **(clip.ruling or {}),
        "pictures": {
            "frames": [
                {"times": [0.0, 0.2], "key": _saved("pair_0.png", (1, 2, 3))},
                {"times": [0.4, 0.6], "key": _saved("pair_1.png", (4, 5, 6))},
            ],
            "sides": [],
        },
    }
    clip.save(update_fields=["ruling"])
    note = _owner_says(ad, "Scene 2 should foam more.")
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        turn(calls=[("look_at_clip", {"scene": 2, "version": None})]),
        turn(calls=[("decide", _decided([_row(2, "fix", "It foams more.")]))]),
    )

    decider.read(ad, [note])

    assert [image.label for image in fake_model.turns_taken[2].images] == [
        "Scene 2, clip of v2 black toilet (the current version), "
        "Frames at 0 s, 0.2 s, 0.4 s, 0.6 s, left to right"
    ]


def test_the_decider_stops_at_the_limit_of_tools_for_one_message(
    fake_model: FakeModel, ad: Job, settings: Any
) -> None:
    settings.MAX_TOOL_CALLS_PER_MESSAGE = 1
    note = _owner_says(ad, "Scene 2 should be brighter.")
    fake_model.respond("decide_fixes", turn(calls=[("sort_message", {"kind": "fix_note"})]))

    with pytest.raises(decider.NoDecision, match="limit of tools"):
        decider.read(ad, [note])

    assert len(fake_model.turns_taken) == 1


def test_a_version_the_scene_doesnt_have_is_refused(fake_model: FakeModel, ad: Job) -> None:
    note = _owner_says(ad, "Go back to the fifth one in scene 2.")
    fake_model.respond(
        "decide_fixes",
        turn(calls=[("sort_message", {"kind": "fix_note"})]),
        turn(calls=[("look_at_version", {"scene": 2, "version": 5})]),
        turn(calls=[("decide", _decided([], question="Which version of scene 2 do you mean?"))]),
    )

    decider.read(ad, [note])

    assert _results("look_at_version") == [
        "Refused: scene 2 has versions 1 to 2, not 5. Nothing was done."
    ]


# What the decider is told the fixing agent can do (fixing agent build, T3; approved by
# Julian 2026-10-19 16:50Z and 16:55Z): pinned word for word, so it changes only on purpose.
# No list of problem kinds and no product words.
WHAT_THE_AGENT_CAN_DO = (
    "- a fix, in a scene that shows the product rather than the person talking. A fixing agent "
    "then owns that scene until the change is made. It sees everything you can see, and it may "
    "change what happens in the scene and how it ends, its starting picture, what is said in "
    "it, its kind of B-roll, whether the person's face shows, and how close the camera is. It "
    "films the scene again up to 6 times, each try checked for the shop owner's change, and "
    "never asks the shop owner anything. When it can't film the change well, the scene stays "
    "as it was and the shop owner is shown its tries. The fix's change is its goal: one "
    "sentence in the shop owner's own words saying what must be different, with any words "
    "they give to say in it word for word."
)


def test_the_decider_is_told_what_the_fixing_agent_can_do_and_offers_no_new_clip() -> None:
    told = " ".join(decider.INSTRUCTIONS.split())
    assert WHAT_THE_AGENT_CAN_DO in told
    assert "look before you decide" in told
    # A talking scene keeps its new picture and new line: the agent films B-roll scenes only.
    assert "in a scene where the person talks to camera, which the fixing agent doesn't film" in (
        told
    )
    assert "new clip" not in told
    assert "new_clip" not in str(decider.SceneDecision.model_json_schema())
