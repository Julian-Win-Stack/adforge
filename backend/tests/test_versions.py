"""A scene's versions and going back to one, in the worker (fix redesign, decisions 8 and 17):
a version is a starting picture the shop owner was shown and the clip made from it, labelled
with the change it was made for. Going back to version N makes a new top version from N's
files: free when N's clip still says the scene's line, or with N's picture copied free and a
new clip made from it when the line changed since. The original can be gone back to. The ad
is made through the chat and its scenes changed by the producer from notes, up to the
go-back, which code calls; the models are faked at the gateway, but the clips are real
tiny videos and ffmpeg runs for real."""

from collections.abc import Callable

import pytest
from django.db.models import Sum
from rest_framework.test import APIClient

from agents.models import ToolCall
from agents.money import dollars
from chat.models import Attachment, Message
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs import library, work
from jobs.models import Job, ProducedItem, Scene, SceneStep

from .conftest import HeldSteps, paid_for, results_of, sent_list_in_the_store
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
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    work.go_back(the_scene(), 1)

    new_picture(fake_model, steps, say, SCENE, "Make the workshop brighter.")

    labels = [version.label for version in work.versions(the_scene())]
    assert labels[3:] == ["v4 back to v1", "v5 Make the workshop brighter."]
    assert work.versions(the_scene())[-1].clip == clip(SCENE)


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


# --- The producer's swap: use_version (spec #247 point 5, ticket #259) -------------------------


def use_version(fake_model: FakeModel, say: Callable[..., None], text: str, number: int) -> str:
    """The shop owner sends `text`; the producer calls use_version on scene 3, then replies.
    Gives what the tool handed back."""
    fake_model.respond(
        "produce",
        turn(calls=[("use_version", {"scene": SCENE, "version": number})]),
        turn(says="Done."),
    )
    say(text)
    return results_of("use_version")[-1]


def test_going_back_to_the_first_version_through_the_chat_pays_nothing_and_assembles_the_ad(
    fake_model: FakeModel, say: Callable[..., None], steps: HeldSteps
) -> None:
    paid, ads_before = paid_for(), len(ads())

    answer = use_version(fake_model, say, f"Go back to the first version of scene {SCENE}.", 1)

    assert answer.startswith(
        f"Scene {SCENE} uses v1 first version again, as v4 back to v1: its clip was brought "
        "back, and nothing was made or paid for. The ad was assembled again with it (version "
        f"{ads_before + 1}, "
    )
    assert answer.endswith("Tell them in one line what changed: it cost nothing.")
    # Nothing made, judged or paid for; nothing started.
    assert paid_for() == paid
    assert steps.held == []
    assert status(SCENE) == "finished"
    assert len(ads()) == ads_before + 1
    scene_cut = next(cut for cut in ads()[-1].cuts if cut["scene"] == SCENE)
    assert ProducedItem.objects.get(pk=scene_cut["clip"]).file == item("clip", 1).file
    assert shown_labels()[-2:] == ["v4 back to v1"] * 2

    # Asked again, the version is already in use: nothing changes.
    again = use_version(fake_model, say, "Use the first one.", 4)
    assert again == (
        f"Scene {SCENE}'s clip in use is already the one of v4 back to v1, so nothing changed."
    )
    assert len(ads()) == ads_before + 1


def test_cost_so_far_adds_up_every_model_call_by_part(
    fake_model: FakeModel, say: Callable[..., None]
) -> None:
    fake_model.respond("produce", turn(calls=[("cost_so_far", {})]), turn(says="Done."))
    say("What has it cost so far?")

    (answer,) = results_of("cost_so_far")
    # Every call up to the tool's: the producer's reply after it came later.
    before_the_reply = ModelCall.objects.exclude(pk=ModelCall.objects.latest("id").pk)
    total = before_the_reply.aggregate(total=Sum("cost_usd"))["total"]
    assert answer.startswith(f"The ad has cost {dollars(total)} so far: scene 1 $")
    assert "scene 2 $" in answer and f"scene {SCENE} $" in answer
    assert "plan_ad $" in answer and "create_person $" in answer
    # Its own turn's cost too, the one that asked included.
    assert "your own turns $" in answer
