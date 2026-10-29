"""The producer edits the plan itself (spec #247 points 5 and 9; ticket #254): any field of
any scene, a scene added, removed or moved. Code marks the steps the edit made stale and
says so in the tool's answer; nothing is made again until the producer calls the scene
tools, and a scene tool asked for a step whose inputs are unchanged hands back what exists.
Driven through the chat from an ad already shown; the producer is scripted and every model
faked at the gateway; the clips are real tiny videos."""

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from django.db.models import Sum
from django.utils import timezone

from agents import tasks
from agents.models import ToolCall
from agents.money import dollars
from chat.models import Attachment
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs import stale
from jobs.models import AgentFix, ProducedItem, Scene, SceneStep

from .conftest import NO_CHOICES, HeldSteps, facts_ok, paid_for
from .test_producer_broll import (
    TALKING_CHOICE,
    checked,  # noqa: F401 (a fixture)
    run,
)
from .test_producer_fixes import shown  # noqa: F401 (a fixture)

pytestmark = pytest.mark.django_db(transaction=True)

Say = Callable[..., None]

AS_BROLL: dict[str, Any] = {
    "broll_kind": "showcase",
    "person_shown": "no face",
    "usage": None,
    "result": None,
}
SHOWS_1 = "The mug on a sunny windowsill, steam rising from it."


def answer_of(tool: str) -> str:
    """What the newest call of `tool` handed back."""
    call = ToolCall.objects.filter(tool=tool).last()
    assert call is not None, f"{tool} was called"
    return call.result


def owner_says(fake_model: FakeModel, say: Say, text: str, *calls: tuple[str, Any]) -> None:
    """The shop owner sends `text`; the producer makes `calls` in one turn, then replies."""
    fake_model.respond("produce", turn(calls=list(calls)), turn(says="Done."))
    say(text)


def test_a_scene_made_broll_has_its_clip_marked_stale_and_nothing_made_again(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    owner_says(
        fake_model,
        say,
        "Make the first scene show the mug instead of her.",
        ("edit_scene", {"scene": 1, "shows": SHOWS_1, **AS_BROLL}),
    )

    scene = Scene.objects.get(number=1)
    assert (scene.shows, scene.broll_kind, scene.person_shown) == (SHOWS_1, "showcase", "no face")
    # What it shows is checked against the page again, and the ad isn't finished until the
    # scene has a new clip.
    assert (scene.fact_checked, scene.status) == (False, "planned")
    answer = answer_of("edit_scene")
    assert "Scene 1" in answer
    assert "clip" in answer and "stale" in answer
    # Its line didn't change: its audio stays.
    assert "audio" not in answer
    # Nothing was started.
    assert steps.held == []
    assert not SceneStep.objects.filter(status="running").exists()


SHOWS_3 = "The mug beside its price tag on a shop shelf."
# A line said over a B-roll scene takes 4 seconds at least: 8 words at the fake voice's 2 a
# second.
LINE_1 = "Meet the Stoneware Mug, hand-thrown by Kiln & Co."
LINE_3 = "Yours today for just $24.00, from Kiln & Co."


def test_make_every_scene_broll_marks_the_clips_stale_and_the_new_clips_make_a_new_ad(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    owner_says(
        fake_model,
        say,
        "Make every scene B-roll.",
        ("edit_scene", {"scene": 1, "line": LINE_1, "shows": SHOWS_1, **AS_BROLL}),
        ("edit_scene", {"scene": 3, "line": LINE_3, "shows": SHOWS_3, **AS_BROLL}),
    )
    # Every scene shows something; the two changed scenes' steps are stale, and the tool
    # said so; scene 2's clip still stands.
    assert all(scene.shows for scene in Scene.objects.all())
    first, third = ToolCall.objects.filter(tool="edit_scene").order_by("id")
    # A scene showing the product has no starting picture to make again (spec #210 point 19).
    every_step = "audio, transcript and clip are stale"
    assert f"Scene 1's {every_step}" in first.result
    assert f"Scene 3's {every_step}" in third.result
    made_again = ["line_audio", "transcript", "clip"]
    assert [stale.stale_steps(scene) for scene in Scene.objects.order_by("number")] == [
        made_again,
        [],
        made_again,
    ]
    paid_before = len(paid_for())

    # The scripted producer has them checked and made again, then assembles.
    fake_model.respond("fact_check", facts_ok(1, 3))
    owner_says(fake_model, say, "Go on", ("run_planning_checks", NO_CHOICES))
    owner_says(
        fake_model,
        say,
        "Go on",
        ("make_line_audio", {"scene": 1}),
        ("make_line_audio", {"scene": 3}),
    )
    run(fake_model, steps)
    owner_says(
        fake_model,
        say,
        "Go on",
        ("transcribe_line_audio", {"scene": 1}),
        ("transcribe_line_audio", {"scene": 3}),
    )
    run(fake_model, steps)
    owner_says(fake_model, say, "Go on", ("make_clip", {"scene": 1}), ("make_clip", {"scene": 3}))
    run(fake_model, steps)
    owner_says(fake_model, say, "Go on", ("assemble_ad", {}))

    # Only the two changed scenes were made again.
    paid = paid_for()[paid_before:]
    assert (paid.count("speak_line"), paid.count("make_broll_clip")) == (2, 2)
    assert "make_talking_clip" not in paid
    ad = ProducedItem.objects.filter(kind="finished_ad").order_by("version").last()
    assert ad is not None and ad.version == 2
    assert [cut["clip"] for cut in ad.cuts][1] == shown.cuts[1]["clip"]
    # The producer is told what the change cost, for its one line to the shop owner.
    spent = ModelCall.objects.filter(
        created_at__gt=shown.created_at, created_at__lt=ad.created_at
    ).aggregate(cost=Sum("cost_usd"))["cost"]
    assert f"The changes since ad version 1 cost {dollars(spent)}" in answer_of("assemble_ad")
    # The shop owner is shown the new ad.
    assert Attachment.objects.filter(file=ad.file).exists()


SHORT_3 = "Just $24.00."


def test_make_it_shorter_with_a_shorter_line_makes_only_that_scenes_audio_and_clip_stale(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    owner_says(fake_model, say, "Make it shorter.", ("edit_scene", {"scene": 3, "line": SHORT_3}))

    # A talking scene keeps its picture for a new line (spec #247 point 9).
    assert [stale.stale_steps(scene) for scene in Scene.objects.order_by("number")] == [
        [],
        [],
        ["line_audio", "transcript", "clip"],
    ]
    assert "Scene 3's audio, transcript and clip are stale" in answer_of("edit_scene")
    paid_before = len(paid_for())

    fake_model.respond("fact_check", facts_ok(3))
    owner_says(fake_model, say, "Go on", ("run_planning_checks", NO_CHOICES))
    owner_says(fake_model, say, "Go on", ("make_line_audio", {"scene": 3}))
    run(fake_model, steps)
    owner_says(fake_model, say, "Go on", ("transcribe_line_audio", {"scene": 3}))
    run(fake_model, steps)
    owner_says(fake_model, say, "Go on", ("make_clip", {"scene": 3}))
    run(fake_model, steps)
    owner_says(fake_model, say, "Go on", ("assemble_ad", {}))

    assert paid_for()[paid_before:] == [
        "fact_check",
        "speak_line",
        "transcribe_line",
        "make_talking_clip",
        "collect_talking_clip",
    ]
    # The clip is made from the picture the scene had.
    clip = ProducedItem.objects.filter(kind="clip", scene__number=3).order_by("version").last()
    assert clip is not None and clip.picture is not None and clip.picture.version == 1
    ad = ProducedItem.objects.get(kind="finished_ad", version=2)
    assert ad.cuts[0]["clip"] == shown.cuts[0]["clip"]
    assert ad.cuts[1]["clip"] == shown.cuts[1]["clip"]


def test_make_it_shorter_by_removing_a_scene_renumbers_the_rest_and_pays_for_nothing(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    third = Scene.objects.get(number=3)
    removed_clip = ProducedItem.objects.get(kind="clip", scene__number=2).file
    owner_says(fake_model, say, "Make it shorter.", ("remove_scene", {"scene": 2}))

    assert list(Scene.objects.order_by("number").values_list("number", "line")) == [
        (1, "Meet the Stoneware Mug from Kiln & Co."),
        (2, "Yours for $24.00."),
    ]
    assert Scene.objects.get(number=2) == third
    answer = answer_of("remove_scene")
    assert "scene 2 removed" in answer
    assert "Nothing made for the ad is stale." in answer
    # The old scene 3's files in the chat are scene 2's card now; the removed scene's are on
    # no card.
    shown_clips = Attachment.objects.filter(kind="video", scene__isnull=False)
    assert set(shown_clips.values_list("scene", flat=True)) == {1, 2}
    assert shown_clips.filter(scene=2, file__in=third.produced.values("file")).exists()
    assert Attachment.objects.get(file=removed_clip).scene is None
    paid_before = len(paid_for())

    owner_says(fake_model, say, "Go on", ("assemble_ad", {}))

    ad = ProducedItem.objects.get(kind="finished_ad", version=2)
    assert [cut["clip"] for cut in ad.cuts] == [shown.cuts[0]["clip"], shown.cuts[2]["clip"]]
    assert paid_for()[paid_before:] == []
    assert "The changes since ad version 1 cost $" in answer_of("assemble_ad")


def test_scenes_moved_keep_what_was_made_for_them_and_their_chat_cards(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
) -> None:
    before = {scene.line: scene.pk for scene in Scene.objects.all()}
    clip_of = {
        scene.pk: ProducedItem.objects.filter(kind="clip", scene=scene).get().file
        for scene in Scene.objects.all()
    }

    owner_says(fake_model, say, "Open on the price.", ("reorder_scenes", {"order": [3, 1, 2]}))

    order = list(Scene.objects.order_by("number").values_list("pk", flat=True))
    assert order == [before["Yours for $24.00."], *[pk for pk in before.values()][:2]]
    assert "Nothing made for the ad is stale." in answer_of("reorder_scenes")
    # Each scene's card shows its own clip under its new number.
    for number, pk in enumerate(order, start=1):
        assert Attachment.objects.get(file=clip_of[pk]).scene == number

    owner_says(fake_model, say, "Go on", ("assemble_ad", {}))
    ad = ProducedItem.objects.get(kind="finished_ad", version=2)
    assert [cut["clip"] for cut in ad.cuts] == [shown.cuts[i]["clip"] for i in (2, 0, 1)]


def test_an_order_that_misses_a_scene_is_refused_and_changes_nothing(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
) -> None:
    owner_says(fake_model, say, "Swap the first two.", ("reorder_scenes", {"order": [2, 1]}))

    assert answer_of("reorder_scenes").startswith("Refused: the new order names each scene once")
    assert list(Scene.objects.order_by("number").values_list("number", flat=True)) == [1, 2, 3]


def test_an_added_scene_is_checked_then_made_and_the_others_move_later(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    line = "It keeps your tea hot for a whole hour."
    owner_says(
        fake_model,
        say,
        "Add a scene about keeping tea hot.",
        ("add_scene", {"at": 2, "line": line}),
    )

    assert list(Scene.objects.order_by("number").values_list("number", "line")) == [
        (1, "Meet the Stoneware Mug from Kiln & Co."),
        (2, line),
        (3, "Hand-thrown, holds 350 ml, and dishwasher safe too."),
        (4, "Yours for $24.00."),
    ]
    added = Scene.objects.get(number=2)
    assert (added.fact_checked, added.status) == (False, "planned")
    answer = answer_of("add_scene")
    assert "scene 2 added" in answer
    assert "The planning checks haven't checked scene 2 against the page" in answer
    # A scene tool won't make anything for it before the checks.
    owner_says(fake_model, say, "Go on", ("make_line_audio", {"scene": 2}))
    assert answer_of("make_line_audio").startswith("Refused: scene 2's line hasn't passed")

    fake_model.respond("fact_check", facts_ok(2))
    owner_says(fake_model, say, "Go on", ("run_planning_checks", NO_CHOICES))
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    owner_says(
        fake_model,
        say,
        "Go on",
        ("make_starting_picture", {"scene": 2, "note": None}),
        ("make_line_audio", {"scene": 2}),
    )
    run(fake_model, steps)
    owner_says(fake_model, say, "Go on", ("transcribe_line_audio", {"scene": 2}))
    run(fake_model, steps)
    owner_says(fake_model, say, "Go on", ("make_clip", {"scene": 2}))
    run(fake_model, steps)
    owner_says(fake_model, say, "Go on", ("assemble_ad", {}))

    ad = ProducedItem.objects.get(kind="finished_ad", version=2)
    assert [cut["scene"] for cut in ad.cuts] == [1, 2, 3, 4]
    assert [ad.cuts[i]["clip"] for i in (0, 2, 3)] == [cut["clip"] for cut in shown.cuts]


def test_a_scene_that_would_break_the_plan_is_refused_with_why_and_nothing_changes(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
) -> None:
    # A scene showing the product needs its labels.
    owner_says(
        fake_model, say, "Show the mug in scene 1.", ("edit_scene", {"scene": 1, "shows": SHOWS_1})
    )

    answer = answer_of("edit_scene")
    assert answer.startswith("Refused: scene 1 wouldn't be a scene the plan can hold")
    assert "A B-roll scene needs its kind" in answer
    scene = Scene.objects.get(number=1)
    assert (scene.shows, scene.fact_checked, scene.status) == ("", True, "finished")


def test_an_edit_taken_back_hands_back_what_was_made_and_pays_for_nothing(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
) -> None:
    line = Scene.objects.get(number=3).line
    owner_says(fake_model, say, "Make it shorter.", ("edit_scene", {"scene": 3, "line": SHORT_3}))
    paid_before = len(paid_for())

    fake_model.respond("fact_check", facts_ok(3))
    owner_says(
        fake_model,
        say,
        "No, keep it as it was.",
        ("edit_scene", {"scene": 3, "line": line}),
        ("run_planning_checks", NO_CHOICES),
        ("make_line_audio", {"scene": 3}),
        ("make_clip", {"scene": 3}),
        ("assemble_ad", {}),
    )

    assert "Nothing made for the ad is stale." in answer_of("edit_scene")
    assert answer_of("make_line_audio").startswith("Scene 3's audio was already made")
    assert answer_of("make_clip").startswith("Scene 3's clip was already made")
    # The ad as it was is shown again, and nothing was made again: only its line was checked.
    assert answer_of("assemble_ad").startswith("The ad was already assembled")
    assert paid_for()[paid_before:] == ["fact_check"]


def test_a_clip_that_finishes_after_its_scene_was_edited_is_stale_and_doesnt_finish_it(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    owner_says(fake_model, say, "Make it shorter.", ("edit_scene", {"scene": 3, "line": SHORT_3}))
    fake_model.respond("fact_check", facts_ok(3))
    owner_says(
        fake_model,
        say,
        "Go on",
        ("run_planning_checks", NO_CHOICES),
        ("make_line_audio", {"scene": 3}),
    )
    run(fake_model, steps)
    owner_says(fake_model, say, "Go on", ("transcribe_line_audio", {"scene": 3}))
    run(fake_model, steps)
    # Its clip is being made when the producer changes the line again.
    owner_says(fake_model, say, "Go on", ("make_clip", {"scene": 3}))
    assert len(steps.held) == 1
    owner_says(
        fake_model, say, "Say it's on sale.", ("edit_scene", {"scene": 3, "line": "On sale now."})
    )
    run(fake_model, steps)

    step = SceneStep.objects.filter(kind="clip", scene__number=3).last()
    assert step is not None and step.status == "finished"
    assert "made for scene 3 as it was before the plan changed" in step.result
    scene = Scene.objects.get(number=3)
    assert scene.status == "planned"
    assert stale.stale_steps(scene) == ["line_audio", "transcript", "clip"]


def test_an_edit_while_the_fixing_agent_films_the_scene_again_is_refused_and_changes_nothing(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
) -> None:
    scene = Scene.objects.get(number=2)
    made = scene.steps.filter(kind=SceneStep.Kind.CLIP).last()
    assert made is not None
    step = SceneStep.objects.create(
        scene=scene,
        kind=SceneStep.Kind.CLIP,
        status=SceneStep.Status.RUNNING,
        tool_call=made.tool_call,
    )
    AgentFix.objects.create(step=step, door=AgentFix.Door.JUDGE)
    line = scene.line
    owner_says(
        fake_model,
        say,
        "Make scene 2 say it's microwave safe.",
        ("edit_scene", {"scene": 2, "line": "Hand-thrown, and microwave safe too."}),
    )

    answer = answer_of("edit_scene")
    assert answer.startswith("Refused: the fixing agent is filming scene 2 again")
    assert Scene.objects.get(number=2).line == line


def test_new_music_once_the_ad_is_shown_is_put_in_when_the_ad_is_assembled_again(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
) -> None:
    # Nothing locks once the ad has clips (spec #247 point 5; ticket #259): the music is made
    # again at any step, and the producer assembles the ad with it.
    job = shown.job
    first = job.produced.filter(kind="music").latest("version")
    owner_says(
        fake_model,
        say,
        "Calmer music, please.",
        ("create_music", {"mood": "calm piano"}),
        ("assemble_ad", {}),
    )

    music = job.produced.filter(kind="music").latest("version")
    assert (music.version, music.text.startswith("Calm piano.")) == (first.version + 1, True)
    assert answer_of("create_music").startswith(f"Made the music (version {music.version}")
    ad = job.produced.filter(kind="finished_ad").latest("version")
    assert (ad.version, ad.made_from) == (shown.version + 1, music)
    assert f"The changes since ad version {shown.version} cost $" in answer_of("assemble_ad")


@pytest.mark.parametrize("edited", [False, True], ids=["clip current", "clip stale"])
def test_a_dead_step_offers_to_keep_the_scenes_clip_only_while_it_is_current(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
    edited: bool,
) -> None:
    if edited:
        owner_says(fake_model, say, "Shorter.", ("edit_scene", {"scene": 3, "line": SHORT_3}))
        fake_model.respond("fact_check", facts_ok(3))
        owner_says(fake_model, say, "Go on", ("run_planning_checks", NO_CHOICES))
    owner_says(
        fake_model,
        say,
        "Make it brighter.",
        ("make_starting_picture", {"scene": 3, "note": "A brighter workshop."}),
    )
    (step_id,) = steps.held
    steps.held.clear()
    long_ago = timezone.now() - timedelta(minutes=6)
    SceneStep.objects.filter(pk=step_id).update(taken_at=long_ago, seen_at=long_ago)

    fake_model.respond("produce", turn(says="Scene 3's picture stopped."))
    tasks.fail_dead_steps()

    result = SceneStep.objects.get(pk=step_id).result
    keeps = "or skip it: scene 3 keeps the clip it has."
    removes = "or skip it: remove scene 3 with remove_scene, and the ad is made without it."
    assert (keeps in result, removes in result) == (not edited, edited)
