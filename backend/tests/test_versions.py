"""A scene's versions and going back to one, in the worker (fix redesign, decisions 8 and 17):
a version is a starting picture the shop owner was shown and the clip made from it, labelled
with the change it was made for. Going back to version N makes a new top version from N's
files: free when N's clip still says the scene's line, or with N's picture copied free and a
new clip made from it when the line changed since. The original can be gone back to. The ad
is made through the chat and its scenes changed through plans the shop owner said yes to, up
to the go-back, which code calls; the models are faked at the gateway, but the clips are real
tiny videos and ffmpeg runs for real."""

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from agents import tasks
from agents.models import ToolCall
from chat.models import Attachment, Message
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs import library, work
from jobs.models import Job, ProducedItem, Scene, SceneStep

from .conftest import HeldSteps, paid_for, sent_list_in_the_store
from .fixing import fixed
from .test_producer_broll import checked  # noqa: F401 (a fixture)
from .test_producer_picture_restore import (  # noqa: F401 (a fixture)
    SCENE,
    SLOWER,
    higher_then_slower,
)
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
# Spec #210 point 19: a scene that shows the product has no starting picture; these run on
# talking scene SCENE (test_producer_picture_restore), whose versions are pictures with clips.


def the_scene() -> Scene:
    return Scene.objects.get(number=SCENE)


def item(kind: str, number: int) -> ProducedItem:
    """Scene 3's `kind` at version `number`."""
    return ProducedItem.objects.get(kind=kind, scene__number=SCENE, version=number)


def shown_labels(number: int = SCENE) -> list[str]:
    """The version label of each of scene 3's pictures and clips in the chat, oldest first."""
    return list(
        Attachment.objects.filter(scene=number)
        .order_by("message__seq", "position")
        .values_list("version", flat=True)
    )


def test_a_scenes_versions_are_its_pictures_with_their_clips_labelled_by_their_change() -> None:
    versions = work.versions(the_scene())

    assert [version.label for version in versions] == [
        "v1 first version",
        f"v2 {HIGHER}",
        f"v3 {SLOWER}",
    ]
    assert [(version.picture, version.clip) for version in versions] == [
        (item("starting_picture", number), item("clip", number)) for number in (1, 2, 3)
    ]
    assert {version.line for version in versions} == {the_scene().line}
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
    earlier = work.versions(the_scene())[number - 1]

    gone = work.go_back(the_scene(), number)

    # Nothing made, judged or paid for: no model was called at all.
    assert ModelCall.objects.count() == calls
    assert not gone.clip_to_make
    top = gone.version
    assert (top.number, top.label) == (4, f"v4 back to v{number}")
    assert top.picture is not None and top.clip is not None
    assert (top.picture.file, top.clip.file) == (earlier.picture.file, earlier.clip.file)  # type: ignore[union-attr]
    assert top.clip.picture == top.picture
    assert work.versions(the_scene())[-1] == top
    assert status(SCENE) == "finished"
    # The chat shows it, so the scene's card shows the version the ad now uses.
    assert shown_labels()[-2:] == [f"v4 back to v{number}"] * 2

    assert assemble(fake_model, say).startswith("Assembled")
    scene_cut = next(cut for cut in ads()[-1].cuts if cut["scene"] == SCENE)
    assert scene_cut["clip"] == top.clip.pk


def test_a_clip_gone_back_to_carries_what_it_was_sent(api: APIClient) -> None:
    sent = sent_list_in_the_store()
    library.keep_sent_list(item("clip", 2), sent)

    work.go_back(the_scene(), 2)

    session = the_scene().job.session
    assert session is not None
    conversation = api.get(f"/api/sessions/{session.pk}/").json()["messages"]
    clip = [
        attached
        for message in conversation
        for attached in message["attachments"]
        if attached["kind"] == "video" and attached["scene"] == SCENE
    ][-1]
    assert clip["version"] == "v4 back to v2"
    assert [each["label"] for each in clip["sent"]["pictures"]] == [
        each.label for each in sent.pictures
    ]


def test_a_newer_change_after_going_back_is_the_next_version(
    fake_model: FakeModel, steps: HeldSteps
) -> None:
    work.go_back(the_scene(), 1)

    new_picture(fake_model, steps, SCENE, "Make the workshop brighter.")

    labels = [version.label for version in work.versions(the_scene())]
    assert labels[3:] == ["v4 back to v1", "v5 Make the workshop brighter."]
    assert work.versions(the_scene())[-1].clip == clip(SCENE)


def change_the_line(fake_model: FakeModel, steps: HeldSteps) -> None:
    """Scene 3 says NEW_LINE, through a plan said yes to: its audio made and transcribed,
    and a new clip made from its picture, version 4."""
    fixed(SCENE, "new_line", change="Say it warmer.", line=NEW_LINE)
    steps.run_held()
    assert the_scene().line == NEW_LINE


def test_going_back_after_the_line_changed_copies_the_picture_and_remakes_its_clip(
    fake_model: FakeModel, steps: HeldSteps
) -> None:
    change_the_line(fake_model, steps)
    earlier = work.versions(the_scene())[1]
    paid = paid_for()

    gone = work.go_back(the_scene(), 2)

    assert paid_for() == paid
    assert gone.clip_to_make
    top = gone.version
    assert top.label == "v5 back to v2"
    assert top.picture is not None and top.picture.file == earlier.picture.file  # type: ignore[union-attr]
    assert top.clip is None
    assert status(SCENE) == "planned"
    assert shown_labels()[-1] == "v5 back to v2"

    # Code then makes its clip from the copied picture with the line as it stands, through
    # the normal clip step.
    fake_model.respond("produce", turn(says="Done."))
    tasks.run_scene_step(clip_step_from(top.picture).pk)

    assert paid_for()[len(paid) :] == ["make_talking_clip", "collect_talking_clip"]
    made = clip(SCENE)
    assert made.picture == top.picture == picture(SCENE)
    assert made.step is not None and made.step.line == NEW_LINE
    assert status(SCENE) == "finished"
    assert work.versions(the_scene())[-1] == work.Version(
        number=5, change="back to v2", picture=top.picture, clip=made, back_to=2
    )


def clip_step_from(picture: ProducedItem) -> SceneStep:
    """Scene 3's clip step from `picture`, started as code starts one after going back."""
    scene = the_scene()
    audio = scene.produced.filter(kind="line_audio").latest("version")
    assert picture.step is not None
    return SceneStep.objects.create(
        scene=scene,
        kind=SceneStep.Kind.CLIP,
        tool_call=ToolCall.objects.filter(agent="producer").latest("id"),
        **SceneStep.snapshot(scene),
        made_from=audio,
        picture=picture,
        picture_step=picture.step,
    )


def test_there_is_no_going_back_to_a_version_that_isnt_there() -> None:
    with pytest.raises(work.NoSuchVersion, match="scene 3 has versions 1 to 3, not 7"):
        work.go_back(the_scene(), 7)
    assert len(work.versions(the_scene())) == 3


def test_there_is_no_going_back_while_the_scenes_picture_or_clip_is_being_made() -> None:
    scene = the_scene()
    SceneStep.objects.create(
        scene=scene,
        kind=SceneStep.Kind.CLIP,
        tool_call=ToolCall.objects.latest("id"),
        **SceneStep.snapshot(scene),
    )
    with pytest.raises(work.SceneBusy):
        work.go_back(scene, 1)
    assert len(work.versions(scene)) == 3


def test_versions_of_a_job_with_no_session_are_still_read() -> None:
    Job.objects.update(session=None)
    assert len(work.versions(the_scene())) == 3
    gone = work.go_back(the_scene(), 1)
    assert gone.version.number == 4
    assert not Message.objects.filter(attachments__version="v4 back to v1").exists()


def test_a_new_clip_of_a_scene_showing_the_product_is_a_version_of_its_own(
    fake_model: FakeModel, steps: HeldSteps
) -> None:
    from .test_producer_auto_fix import HOVERS, NOTED

    # Spec #210 point 19: B-roll scene 2 has no starting picture; its versions are its clips.
    fixed(2, "new_clip", change=HOVERS)
    fake_model.respond(
        "rewrite_clip_prompt",
        {"motion_prompt": NOTED, "motion_prompt_reason": "The note asks for the pour."},
    )
    steps.run_held()

    broll = Scene.objects.get(number=2)
    versions = work.versions(broll)
    assert [version.label for version in versions] == ["v1 first version", f"v2 {HOVERS}"]
    assert (versions[1].picture, versions[1].clip) == (None, clip(2))
    # Going back to the clip before it is free: same line.
    gone = work.go_back(broll, 1)
    assert not gone.clip_to_make
    assert gone.version.clip is not None and gone.version.clip.file == versions[0].clip.file  # type: ignore[union-attr]


def test_going_back_after_the_voice_changed_copies_the_picture_and_remakes_its_clip() -> None:
    # A clip in the person's old voice isn't the ad's clip any more, however the line reads.
    job = Job.objects.get()
    voice = work.latest(job, ProducedItem.Kind.VOICE)
    assert voice is not None
    ProducedItem.objects.create(
        job=job, kind=ProducedItem.Kind.VOICE, version=voice.version + 1, file=voice.file
    )
    paid = paid_for()

    gone = work.go_back(the_scene(), 1)

    assert paid_for() == paid
    assert gone.clip_to_make
    assert gone.version.label == "v4 back to v1" and gone.version.clip is None
    assert status(SCENE) == "planned"


def test_a_version_with_no_picture_cant_be_gone_back_to_once_the_line_changed(
    fake_model: FakeModel, steps: HeldSteps
) -> None:
    # A talking scene's version with no starting picture: once its line changed there is
    # nothing to bring back and nothing to make a clip from.
    ProducedItem.objects.filter(scene__number=SCENE, kind="clip").update(picture=None)
    SceneStep.objects.filter(scene__number=SCENE, kind=SceneStep.Kind.STARTING_PICTURE).update(
        status=SceneStep.Status.STOPPED
    )
    assert [version.picture for version in work.versions(the_scene())] == [None] * 3
    change_the_line(fake_model, steps)
    before = (ProducedItem.objects.count(), Message.objects.count(), status(SCENE))

    with pytest.raises(work.NoSuchVersion, match="version 1 has no starting picture"):
        work.go_back(the_scene(), 1)

    assert (ProducedItem.objects.count(), Message.objects.count(), status(SCENE)) == before


def test_a_long_change_is_shown_with_its_whole_label(
    fake_model: FakeModel, steps: HeldSteps
) -> None:
    from .test_producer_auto_fix import HOVERS, NOTED

    # A fix folds in every note the shop owner wrote, so its words can run long.
    long = f"{HOVERS} {'The steam should rise slowly from the tea as it settles. ' * 8}".strip()
    fixed(2, "new_clip", change=long)
    fake_model.respond(
        "rewrite_clip_prompt",
        {"motion_prompt": NOTED, "motion_prompt_reason": "The note asks for the pour."},
    )
    steps.run_held()

    assert len(long) > 300
    assert clip(2).step.status == SceneStep.Status.FINISHED  # type: ignore[union-attr]
    assert shown_labels(2)[-1] == f"v2 {long}"
