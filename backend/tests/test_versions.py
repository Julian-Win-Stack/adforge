"""A scene's versions and going back to one, in the worker (fix redesign, decisions 8 and 17):
a version is a starting picture the shop owner was shown and the clip made from it, labelled
with the change it was made for. Going back to version N makes a new top version from N's
files: free when N's clip still says the scene's line, or with N's picture copied free and a
new clip made from it when the line changed since. The original can be gone back to. Driven
through the chat up to the go-back, which code calls; the models are faked at the gateway,
but the clips are real tiny videos and ffmpeg runs for real."""

from collections.abc import Callable

import pytest

from agents import tasks
from agents.models import ToolCall
from chat.models import Attachment, Message
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs import work
from jobs.models import Job, ProducedItem, Scene, SceneStep

from .conftest import JUDGED_CLIP, HeldSteps, paid_for, said_yes_to_a_fix
from .test_producer_broll import calling, checked, run  # noqa: F401 (a fixture)
from .test_producer_picture_restore import SLOWER, higher_then_slower  # noqa: F401 (a fixture)
from .test_producer_picture_then_clip import (
    HIGHER,
    ads,
    assemble,
    assembled,  # noqa: F401 (a fixture)
    clip,
    new_picture,
    picture,
    status,
)

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("checked", "higher_then_slower"),
]

NEW_LINE = "Stoneware that keeps your tea hot."


def scene_2() -> Scene:
    return Scene.objects.get(number=2)


def item(kind: str, number: int) -> ProducedItem:
    """Scene 2's `kind` at version `number`."""
    return ProducedItem.objects.get(kind=kind, scene__number=2, version=number)


def shown_labels() -> list[str]:
    """The version label of each of scene 2's pictures and clips in the chat, oldest first."""
    return list(
        Attachment.objects.filter(scene=2)
        .order_by("message__seq", "position")
        .values_list("version", flat=True)
    )


def test_a_scenes_versions_are_its_pictures_with_their_clips_labelled_by_their_change() -> None:
    versions = work.versions(scene_2())

    assert [version.label for version in versions] == [
        "v1 first version",
        f"v2 {HIGHER}",
        f"v3 {SLOWER}",
    ]
    assert [(version.picture, version.clip) for version in versions] == [
        (item("starting_picture", number), item("clip", number)) for number in (1, 2, 3)
    ]
    assert {version.line for version in versions} == {scene_2().line}
    # The chat's scene card shows each picture and clip with its version.
    assert shown_labels() == [
        "v1 first version",
        "v1 first version",
        f"v2 {HIGHER}",
        f"v2 {HIGHER}",
        f"v3 {SLOWER}",
        f"v3 {SLOWER}",
    ]


@pytest.mark.parametrize("number", [2, 1], ids=["an earlier version", "the original"])
def test_going_back_with_the_line_unchanged_is_a_new_top_version_for_nothing(
    fake_model: FakeModel, say: Callable[..., None], number: int
) -> None:
    calls = ModelCall.objects.count()
    earlier = work.versions(scene_2())[number - 1]

    gone = work.go_back(scene_2(), number)

    # Nothing made, judged or paid for: no model was called at all.
    assert ModelCall.objects.count() == calls
    assert not gone.clip_to_make
    top = gone.version
    assert (top.number, top.label) == (4, f"v4 back to v{number}")
    assert top.picture is not None and top.clip is not None
    assert (top.picture.file, top.clip.file) == (earlier.picture.file, earlier.clip.file)  # type: ignore[union-attr]
    assert top.clip.picture == top.picture
    assert work.versions(scene_2())[-1] == top
    assert status(2) == "finished"
    # The chat shows it, so the scene's card shows the version the ad now uses.
    assert shown_labels()[-2:] == [f"v4 back to v{number}"] * 2

    assert assemble(fake_model, say).startswith("Assembled")
    scene_cut = next(cut for cut in ads()[-1].cuts if cut["scene"] == 2)
    assert scene_cut["clip"] == top.clip.pk


def test_a_newer_change_after_going_back_is_the_next_version(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    work.go_back(scene_2(), 1)

    new_picture(fake_model, steps, say, 2, "Make the workshop brighter.")
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    run(fake_model, steps)

    labels = [version.label for version in work.versions(scene_2())]
    assert labels[3:] == ["v4 back to v1", "v5 Make the workshop brighter."]
    assert work.versions(scene_2())[-1].clip == clip(2)


def test_going_back_after_the_line_changed_copies_the_picture_and_remakes_its_clip(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    said_yes_to_a_fix(2, f'Scene 2 says "{NEW_LINE}"', "line")
    calling(fake_model, say, ("change_line", {"scene": 2, "line": NEW_LINE}))
    calling(fake_model, say, ("make_line_audio", {"scene": 2}))
    run(fake_model, steps)
    calling(fake_model, say, ("transcribe_line_audio", {"scene": 2}))
    run(fake_model, steps)
    earlier = work.versions(scene_2())[1]
    paid = paid_for()

    gone = work.go_back(scene_2(), 2)

    assert paid_for() == paid
    assert gone.clip_to_make
    top = gone.version
    assert top.label == "v4 back to v2"
    assert top.picture is not None and top.picture.file == earlier.picture.file  # type: ignore[union-attr]
    assert top.clip is None
    assert status(2) == "planned"
    assert shown_labels()[-1] == "v4 back to v2"

    # Code then makes its clip from the copied picture with the line as it stands, through
    # the normal clip step, which the clip judge checks as any.
    fake_model.respond("produce", turn(says="Done."))
    tasks.run_scene_step(clip_step_from(top.picture).pk)

    assert paid_for()[len(paid) :] == ["make_broll_clip", "collect_broll_clip", *JUDGED_CLIP]
    made = clip(2)
    assert made.picture == top.picture == picture(2)
    assert made.ruling is not None and made.ruling["verdict"] == "pass"
    assert made.step is not None and made.step.line == NEW_LINE
    assert status(2) == "finished"
    assert work.versions(scene_2())[-1] == work.Version(
        number=4, change="back to v2", picture=top.picture, clip=made, back_to=2
    )


def clip_step_from(picture: ProducedItem) -> SceneStep:
    """Scene 2's clip step from `picture`, started as code starts one after going back."""
    scene = scene_2()
    audio = scene.produced.filter(kind="line_audio").latest("version")
    assert picture.step is not None
    return SceneStep.objects.create(
        scene=scene,
        kind=SceneStep.Kind.CLIP,
        tool_call=ToolCall.objects.latest("id"),
        **SceneStep.made_for(scene),
        made_from=audio,
        picture=picture,
        picture_step=picture.step,
    )


def test_there_is_no_going_back_to_a_version_that_isnt_there() -> None:
    with pytest.raises(work.NoSuchVersion, match="scene 2 has versions 1 to 3, not 7"):
        work.go_back(scene_2(), 7)
    assert len(work.versions(scene_2())) == 3


def test_there_is_no_going_back_while_the_scenes_picture_or_clip_is_being_made() -> None:
    scene = scene_2()
    SceneStep.objects.create(
        scene=scene,
        kind=SceneStep.Kind.CLIP,
        tool_call=ToolCall.objects.latest("id"),
        **SceneStep.made_for(scene),
    )
    with pytest.raises(work.SceneBusy):
        work.go_back(scene, 1)
    assert len(work.versions(scene)) == 3


def test_an_earlier_note_brought_back_by_the_producer_is_still_a_version_of_its_own(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    # The exact-same-note reuse (#143) keeps working, and what it brings back is shown as a
    # new version with the change it was made for.
    paid = paid_for()
    new_picture(fake_model, steps, say, 2, HIGHER)
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    run(fake_model, steps)

    assert paid_for() == paid
    top = work.versions(scene_2())[-1]
    assert (top.number, top.label) == (4, f"v4 {HIGHER}")
    assert top.picture is not None and top.clip is not None
    assert (top.picture.file, top.clip.file) == (
        item("starting_picture", 2).file,
        item("clip", 2).file,
    )


def test_versions_of_a_job_with_no_session_are_still_read() -> None:
    Job.objects.update(session=None)
    assert len(work.versions(scene_2())) == 3
    gone = work.go_back(scene_2(), 1)
    assert gone.version.number == 4
    assert not Message.objects.filter(attachments__version="v4 back to v1").exists()


def test_a_new_clip_of_the_same_picture_is_a_version_of_its_own(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    from .test_producer_auto_fix import HOVERS, NOTED

    said_yes_to_a_fix(2, HOVERS, "clip")
    fake_model.respond(
        "rewrite_clip_prompt",
        {"motion_prompt": NOTED, "motion_prompt_reason": "The note asks for the pour."},
    )
    calling(fake_model, say, ("make_clip", {"scene": 2, "note": HOVERS}))
    run(fake_model, steps)

    versions = work.versions(scene_2())
    assert [version.label for version in versions][3:] == [f"v4 {HOVERS}"]
    assert (versions[3].picture, versions[3].clip) == (versions[2].picture, clip(2))
    # Going back to the clip before it is free: same picture, same line.
    gone = work.go_back(scene_2(), 3)
    assert not gone.clip_to_make
    assert gone.version.clip is not None and gone.version.clip.file == versions[2].clip.file  # type: ignore[union-attr]
