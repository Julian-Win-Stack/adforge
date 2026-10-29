"""The fixing agent's place_edit move (#237, spec #210 point 23): after a judge fail the agent
may change where the scene it fixes happens, its choice, never forced. It writes either a
small edit of the scene's room or person-in-room picture, or the description of a new place
drawn fresh; code makes the matching picture for that scene only (the other scenes keep the
ad's), shows it to the agent and films nothing: the agent looks, redoes it or films again,
and the next try sends it. The agent, the judge, the pictures and the clips are faked at the
gateway."""

from collections.abc import Callable
from typing import Any

import pytest

from agents.fixing_agent import Failure, Outcome, fix_scene
from agents.models import ToolCall
from gateway.fake import FakeModel, turn
from gateway.gateway import edit_picture
from gateway.types import ToolUse
from jobs import work
from jobs.library import sent_list_of
from jobs.models import AgentFixTry, ProducedItem, Scene, SceneStep

from .conftest import HeldSteps, WorkerStopped, handoffs
from .test_fixing_agent import film_again
from .test_producer_auto_fix import clip_of_scene_2
from .test_producer_broll import checked, instructed  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db(transaction=True)

KITCHEN = "A bright kitchen with a wooden table under a window, a kettle on the counter."
CLEAR = "Clear the table: take everything off it."


@pytest.fixture
def failed_clip(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> SceneStep:
    """Scene 2's clip, made and judged; the ad has its person-in-room picture and room alone."""
    return clip_of_scene_2(fake_model, steps, say)


def fixed(step: SceneStep) -> Outcome:
    clip = step.produced.order_by("id").first()
    assert clip is not None
    return fix_scene(step, door="judge", failure=Failure(ruling=clip.ruling), goal=None)


def place_edit(**asked: Any) -> tuple[str, dict[str, Any]]:
    return ("place_edit", {"picture": "person_in_room", "edit": None, "new_place": None, **asked})


def the_ads(kind: ProducedItem.Kind) -> ProducedItem:
    made = work.latest(Scene.objects.get(number=2).job, kind)
    assert made is not None
    return made


def clips() -> int:
    return ProducedItem.objects.filter(kind=ProducedItem.Kind.CLIP).count()


def place_edits(fake_model: FakeModel) -> list[ToolUse]:
    """The place_edit results in the agent's last turn."""
    last = [each for each in fake_model.turns_taken if each.purpose == "fix_scene"][-1]
    return [
        each
        for each in last.handoff.conversation
        if isinstance(each, ToolUse) and each.tool == "place_edit"
    ]


def test_a_new_place_is_drawn_for_the_scene_only_and_shown_before_anything_films(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    ads_person = the_ads(ProducedItem.Kind.PERSON_IN_ROOM)
    ads_room = the_ads(ProducedItem.Kind.ROOM_ALONE)
    portrait = the_ads(ProducedItem.Kind.PORTRAIT)
    filmed_before = clips()
    fake_model.respond("fix_scene", turn(calls=[place_edit(new_place=KITCHEN)]), WorkerStopped())
    with pytest.raises(WorkerStopped):
        fixed(failed_clip)

    # Nothing filmed: the agent is shown the new picture first.
    assert clips() == filmed_before
    scene = Scene.objects.get(number=2)
    own, own_room = work.scene_place(scene, "has face"), work.scene_place(scene, "no face")
    assert own[0] == "person_in_room" and own_room[0] == "room"
    assert own[1] is not None and own[1].pk != ads_person.pk
    assert own_room[1] is not None and own_room[1].pk != ads_room.pk
    # The new room is drawn fresh from the agent's words, the person in it from the portrait.
    [drawn] = handoffs("draw_scene_room")
    assert KITCHEN in drawn["prompt"]
    [person] = handoffs("make_scene_person_in_room")
    assert person["pictures"] == [portrait.file, own_room[1].file]
    [shown] = place_edits(fake_model)
    assert [picture.key for picture in shown.pictures] == [own[1].file]
    # The other scenes keep the ad's.
    others = Scene.objects.exclude(number=2)
    assert others.exists()
    for other in others:
        assert work.scene_place(other, "has face") == ("person_in_room", ads_person)
        assert work.scene_place(other, "no face") == ("room", ads_room)


A_FACE = film_again(person_shown="has face")


def sent_place() -> str:
    """The file of the place the newest try's clip was sent: its drawn picture."""
    row = AgentFixTry.objects.exclude(clip=None).order_by("-number").first()
    assert row is not None and row.clip is not None
    sent = sent_list_of(row.clip)
    assert sent is not None
    [place] = [picture for picture in sent.pictures if picture.drawn is not None]
    return place.file


def test_after_a_restart_the_next_try_is_drawn_in_the_scenes_own_place(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    before_picture = the_ads(ProducedItem.Kind.BEFORE_PICTURE)
    fake_model.respond(
        "fix_scene",
        turn(calls=[place_edit(new_place=KITCHEN)]),
        WorkerStopped(),
        turn(calls=[A_FACE]),
    )
    with pytest.raises(WorkerStopped):
        fixed(failed_clip)
    fixed(failed_clip)

    scene = Scene.objects.get(number=2)
    _, own = work.scene_place(scene, "has face")
    assert own is not None and own.scene == scene
    # The restart drew nothing again.
    assert len(handoffs("draw_scene_room")) == len(handoffs("make_scene_person_in_room")) == 1
    assert ToolCall.objects.filter(tool="place_edit").count() == 1
    # The try is sent the scene's own place, not the ad's.
    assert sent_place() == own.file != before_picture.file


MOVE_THE_LAMP = "Move the lamp to the left end of the shelf."


def test_a_small_edit_changes_the_scenes_own_picture_only_and_is_shown(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    ads_room = the_ads(ProducedItem.Kind.ROOM_ALONE)
    filmed_before = clips()
    fake_model.respond(
        "fix_scene",
        turn(calls=[place_edit(picture="room", edit=CLEAR)]),
        turn(calls=[place_edit(picture="room", edit=MOVE_THE_LAMP)]),
        WorkerStopped(),
    )
    with pytest.raises(WorkerStopped):
        fixed(failed_clip)

    assert clips() == filmed_before
    first, second = handoffs("edit_scene_place")
    # The first edit is of the picture the scene had, the ad's; the next, of the scene's own.
    assert CLEAR in first["prompt"] and first["pictures"] == [ads_room.file]
    scene = Scene.objects.get(number=2)
    edited = scene.produced.filter(kind=ProducedItem.Kind.SCENE_ROOM).order_by("version")
    assert [each.version for each in edited] == [1, 2]
    assert MOVE_THE_LAMP in second["prompt"] and second["pictures"] == [edited[0].file]
    assert work.scene_place(scene, "no face") == ("room", edited[1])
    shown = [[picture.key for picture in each.pictures] for each in place_edits(fake_model)]
    assert shown == [[edited[0].file], [edited[1].file]]
    # Nothing new was drawn, and the other scenes keep the ad's room.
    assert handoffs("draw_scene_room") == []
    for other in Scene.objects.exclude(number=2):
        assert work.scene_place(other, "no face") == ("room", ads_room)


@pytest.mark.parametrize(
    ("asked", "refused"),
    [
        ({"edit": CLEAR, "new_place": KITCHEN}, "write an edit or a new place, not both."),
        ({"edit": None, "new_place": None}, "write an edit or a new place. Nothing"),
        ({"edit": "  ", "new_place": None}, "the words are blank: write the edit or the new"),
    ],
)
def test_place_edit_takes_one_edit_or_one_new_place(
    fake_model: FakeModel, failed_clip: SceneStep, asked: dict[str, Any], refused: str
) -> None:
    fake_model.respond("fix_scene", turn(calls=[place_edit(**asked)]), turn(calls=[film_again()]))
    fixed(failed_clip)
    result = ToolCall.objects.get(tool="place_edit").result
    assert result.startswith(f"Refused: {refused}")
    assert handoffs("edit_scene_place") == handoffs("draw_scene_room") == []


def test_a_new_place_for_the_presenter_with_no_presenter_is_refused_before_paying(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    # An ad whose presenter was never made.
    ProducedItem.objects.filter(kind=ProducedItem.Kind.PORTRAIT).update(
        kind=ProducedItem.Kind.MUSIC
    )
    fake_model.respond(
        "fix_scene", turn(calls=[place_edit(new_place=KITCHEN)]), turn(calls=[film_again()])
    )
    fixed(failed_clip)
    result = ToolCall.objects.get(tool="place_edit").result
    assert result.startswith("Refused: the ad has no presenter yet")
    assert handoffs("draw_scene_room") == []
    assert not Scene.objects.get(number=2).produced.filter(kind=ProducedItem.Kind.SCENE_ROOM)


def tries() -> int:
    return AgentFixTry.objects.count()


NO_FACE_NEW_PICTURE = film_again()


def test_a_try_needing_the_picture_the_new_place_lacks_is_refused_and_names_it(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    fake_model.respond(
        "fix_scene",
        turn(calls=[place_edit(picture="room", new_place=KITCHEN)]),
        turn(calls=[A_FACE]),
        turn(calls=[("look_at_place", {})]),
        turn(calls=[NO_FACE_NEW_PICTURE]),
    )
    fixed(failed_clip)

    calls = list(ToolCall.objects.filter(tool="film_again").order_by("id"))
    assert calls[0].result.startswith(
        "Refused: the scene's newest place is its own picture of its place on its own, and this "
        "try needs its picture of the presenter in its place: call place_edit with picture "
        "person_in_room first."
    )
    face, no_face = calls
    # Nothing was drawn for it; the room alone is sent to a try showing nobody.
    assert handoffs("make_scene_person_in_room") == []
    [room] = Scene.objects.get(number=2).produced.filter(kind=ProducedItem.Kind.SCENE_ROOM)
    assert not no_face.result.startswith("Refused")
    assert sent_place() == room.file
    # The place the agent looks at is the scene's own.
    looked = ToolCall.objects.get(tool="look_at_place")
    assert [picture["key"] for picture in looked.pictures] == [room.file]


def test_a_worker_stopped_mid_place_edit_pays_for_nothing_again(
    fake_model: FakeModel, failed_clip: SceneStep, monkeypatch: pytest.MonkeyPatch
) -> None:
    stopped: list[str] = []

    def stopping_once(*, purpose: str, **asked: Any) -> str:
        drawn = edit_picture(purpose=purpose, **asked)
        if purpose == "make_scene_person_in_room" and not stopped:
            stopped.append(drawn)
            raise WorkerStopped()
        return drawn

    monkeypatch.setattr("jobs.work.edit_picture", stopping_once)
    fake_model.respond(
        "fix_scene", turn(calls=[place_edit(new_place=KITCHEN)]), turn(calls=[A_FACE])
    )
    with pytest.raises(WorkerStopped):
        fixed(failed_clip)
    fixed(failed_clip)

    # The place_edit run again drew nothing it had paid for, and kept one of each.
    assert len(handoffs("draw_scene_room")) == len(handoffs("make_scene_person_in_room")) == 1
    scene = Scene.objects.get(number=2)
    assert scene.produced.filter(kind=ProducedItem.Kind.SCENE_ROOM).count() == 1
    [own] = scene.produced.filter(kind=ProducedItem.Kind.SCENE_PERSON_IN_ROOM)
    assert own.file == stopped[0]
    assert sent_place() == own.file
