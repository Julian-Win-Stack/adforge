"""A B-roll scene with needs, made way 3, driven through the chat: no picture is made. Code
picks the example pictures (the main photo, a photo for each need, then the portrait for a
scene that shows the presenter's face), the model writing the scene's prompt gives each
its job in a slot of its own, and the clip is asked for from those pictures, with no
starting picture. The producer's model and the scene models are faked at the gateway, but
the clips are real tiny videos and ffmpeg runs for real."""

from collections.abc import Callable
from typing import Any

import pytest

from adforge import file_store
from agents import tasks
from chat.models import Attachment
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs.models import Job, ProducedItem, ProductPhoto, Scene, SceneStep

from .conftest import HeldSteps, WorkerStopped, handoffs, paid_for, photo, results_of
from .test_producer_broll import (
    calling,
    checked,  # noqa: F401 (a fixture)
    clip_of_scene_2,
    clips_asked,
    instructed,  # noqa: F401 (a fixture)
    made_ready,
    run,
)

# Each request commits on its own, as on the real server, and so does the producer's work.
# Every test starts from a chat whose ad is planned with scene 2 showing the mug, has its
# person, and whose lines passed the fact check.
pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("checked")]

# What scene 2 needs that the mug's photo can't show: the tea's colour, which photo 3 (a
# woman pouring it) and photo 4 (the pour alone) both show.
NEEDS = [{"what": "the tea's colour", "photos": [3, 4]}]

# The model's answer for scene 2 with its main photo and one photo for its need.
WAY_3_CHOICE: dict[str, Any] = {
    "photo": 1,
    "photo_reason": "The front of the mug shows its glaze as the tea is poured.",
    "image_1": {"job": "the mug; keep its glaze exactly", "ignore": None},
    "image_2": {"job": "only the tea's colour", "ignore": None},
    "action": "Tea is poured into the mug on a sunny workbench, and steam rises.",
    "prompt_reason": "It shows the pour the line describes.",
}

VIDEO_PROMPT = (
    "Image 1 is the mug; keep its glaze exactly. Image 2 is only the tea's colour. Tea is "
    "poured into the mug on a sunny workbench, and steam rises."
)


def add_photos(*faces: bool) -> None:
    """Add a photo to the job for each of `faces`, numbered from 3, each with a stranger's
    face in it or not. None shows the mug in the ad's colour."""
    job = Job.objects.get()
    for number, face in enumerate(faces, start=3):
        ProductPhoto.objects.create(
            job=job,
            position=number,
            file=file_store.save(f"photo-{number}.png", photo(number)),
            has_face=face,
        )


def photo_file(number: int) -> str:
    return Job.objects.get().photos.get(position=number).file


def portrait_file() -> str:
    return Job.objects.get().produced.get(kind="portrait").file


def needing(needs: list[dict[str, Any]], **labels: Any) -> None:
    """Give scene 2 `needs`, and any other B-roll labels."""
    Scene.objects.filter(number=2).update(needs=needs, **labels)


def picture_step_of_scene_2(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], *answers: dict[str, Any]
) -> SceneStep:
    """Have the producer make scene 2's starting picture, the model answering `answers`."""
    calling(fake_model, say, ("make_starting_picture", {"scene": 2, "note": None}))
    fake_model.respond("choose_broll_picture", *answers)
    run(fake_model, steps)
    return SceneStep.objects.filter(kind="starting_picture", scene__number=2).last()  # type: ignore[return-value]


@pytest.fixture
def way_3(fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> SceneStep:
    """Scene 2's picture step, made way 3: photo 3 has a woman's face, photo 4 doesn't."""
    add_photos(True, False)
    needing(NEEDS)
    return picture_step_of_scene_2(fake_model, steps, say, WAY_3_CHOICE)


# --- The picture step --------------------------------------------------------------------------


def test_a_scene_with_needs_makes_no_picture(way_3: SceneStep) -> None:
    assert way_3.status == "finished"
    assert list(ProducedItem.objects.filter(kind="starting_picture")) == []
    assert "make_starting_picture" not in paid_for()


def test_the_picture_step_stores_way_3_its_pictures_with_their_jobs_and_the_video_prompt(
    way_3: SceneStep,
) -> None:
    assert (way_3.way, way_3.pictures_sent, way_3.motion_prompt, way_3.prompt) == (
        3,
        [
            {
                "image": 1,
                "photo": 1,
                "job": "the mug; keep its glaze exactly",
                "file": photo_file(1),
            },
            {"image": 2, "photo": 4, "job": "only the tea's colour", "file": photo_file(4)},
        ],
        VIDEO_PROMPT,
        "",
    )
    assert (way_3.photo.position if way_3.photo else None, way_3.photo_reason) == (
        1,
        WAY_3_CHOICE["photo_reason"],
    )
    assert way_3.prompt_reason == WAY_3_CHOICE["prompt_reason"]


def test_the_producer_is_told_the_scene_is_ready_for_its_clip_and_the_chat_shows_nothing(
    way_3: SceneStep,
) -> None:
    assert way_3.result == (
        "Background step finished: Scene 2 is ready for its clip. Photo 1 was used: "
        f"{WAY_3_CHOICE['photo_reason']}"
    )
    # The chat shows no picture for it: only the portrait, before, was shown.
    assert not Attachment.objects.filter(message__created_at__gte=way_3.started_at).exists()


def test_the_model_is_shown_the_photos_it_picks_from_then_the_photos_for_the_needs(
    way_3: SceneStep,
) -> None:
    # Photo 1 is the only photo in the ad's colour; photo 4 is the need's photo without a
    # face.
    assert ModelCall.objects.get(purpose="choose_broll_picture").images == [
        {"label": "Photo 1", "key": photo_file(1)},
        {"label": "Photo 4", "key": photo_file(4)},
    ]
    (planned,) = handoffs("choose_broll_picture")
    assert (planned["colour_photos"], planned["needs"], planned["portrait"]) == (
        [1],
        [{"what": ["the tea's colour"], "photo": 4, "has_face": False}],
        False,
    )


def test_the_model_writing_the_prompt_is_told_there_is_no_starting_picture(
    fake_model: FakeModel,
    instructed: dict[str, list[str]],  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    add_photos(True, False)
    needing(NEEDS)

    picture_step_of_scene_2(fake_model, steps, say, WAY_3_CHOICE)

    (told,) = instructed["choose_broll_picture"]
    assert "No picture is made for this scene" in told
    assert "Then write the picture model's prompt" not in told


def test_a_need_whose_photos_all_have_a_face_sends_the_first_with_what_to_ignore(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    add_photos(True, True)
    needing([{"what": "the tea's colour", "photos": [4, 3]}])
    answer = {**WAY_3_CHOICE, "image_2": {"job": "only the tea's colour", "ignore": "the woman"}}

    step = picture_step_of_scene_2(fake_model, steps, say, answer)

    assert step.pictures_sent[1] == {
        "image": 2,
        "photo": 4,
        "job": "only the tea's colour; ignore the woman",
        "file": photo_file(4),
    }
    assert "Image 2 is only the tea's colour; ignore the woman. " in step.motion_prompt


def test_a_photo_with_a_face_whose_slot_says_nothing_to_ignore_is_refused(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    add_photos(True)
    needing([{"what": "the tea's colour", "photos": [3]}])

    step = picture_step_of_scene_2(fake_model, steps, say, WAY_3_CHOICE)

    assert step.status == "failed"
    assert "Image 2 shows a stranger's face" in step.reason


def test_the_portrait_is_sent_last_for_a_scene_that_shows_the_presenters_face(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    add_photos(True, False)
    needing(NEEDS, person_shown="has face")
    answer = {**WAY_3_CHOICE, "image_3": {"job": "the presenter", "ignore": None}}

    step = picture_step_of_scene_2(fake_model, steps, say, answer)

    assert [{**picture, "file": None} for picture in step.pictures_sent] == [
        {"image": 1, "photo": 1, "job": "the mug; keep its glaze exactly", "file": None},
        {"image": 2, "photo": 4, "job": "only the tea's colour", "file": None},
        {"image": 3, "portrait": True, "job": "the presenter", "file": None},
    ]
    assert step.pictures_sent[2]["file"] == portrait_file()
    assert ModelCall.objects.get(purpose="choose_broll_picture").images[-1] == {
        "label": "The portrait",
        "key": portrait_file(),
    }
    assert "Image 3 is the presenter. Tea is poured" in step.motion_prompt


def test_never_more_than_5_pictures_are_sent_the_portrait_kept(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    add_photos(False, False, False, False, False)
    needing(
        [{"what": f"need {number}", "photos": [number]} for number in (3, 4, 5, 6, 7)],
        person_shown="has face",
    )
    answer = {
        **WAY_3_CHOICE,
        **{f"image_{n}": {"job": f"job {n}", "ignore": None} for n in (2, 3, 4)},
        "image_5": {"job": "the presenter", "ignore": None},
    }

    step = picture_step_of_scene_2(fake_model, steps, say, answer)

    assert [(p["image"], p.get("photo"), p.get("portrait")) for p in step.pictures_sent] == [
        (1, 1, None),
        (2, 3, None),
        (3, 4, None),
        (4, 5, None),
        (5, None, True),
    ]


def test_a_needs_photo_that_is_the_main_photo_isnt_sent_twice(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    add_photos(False)
    job = Job.objects.get()
    # Photo 3 shows the mug in the ad's colour too, and the tea in it.
    job.photos.filter(position=3).update(shows_product_colour=True)
    needing([{"what": "the tea's colour", "photos": [3]}], person_shown="has face")
    # The model picks photo 3 as the main photo: the portrait is then Image 2.
    answer = {
        **WAY_3_CHOICE,
        "photo": 3,
        "image_1": {"job": "the mug and the tea's colour", "ignore": None},
        "image_2": {"job": "the presenter", "ignore": None},
        "image_3": None,
    }

    step = picture_step_of_scene_2(fake_model, steps, say, answer)

    assert [
        (p["image"], p.get("photo"), p.get("portrait"), p["job"]) for p in step.pictures_sent
    ] == [
        (1, 3, None, "the mug and the tea's colour"),
        (2, None, True, "the presenter"),
    ]
    assert step.motion_prompt.startswith(
        "Image 1 is the mug and the tea's colour. Image 2 is the presenter. Tea is poured"
    )


def test_an_answer_missing_a_pictures_slot_is_refused_while_it_is_read(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    add_photos(True, False)
    needing(NEEDS)
    answer = {key: value for key, value in WAY_3_CHOICE.items() if key != "image_2"}

    step = picture_step_of_scene_2(fake_model, steps, say, answer)

    assert step.status == "failed"
    assert "image_2" in step.reason
    assert (step.way, step.motion_prompt) == (None, "")


def test_asked_again_for_the_same_scene_nothing_is_planned_or_paid_for_again(
    fake_model: FakeModel, way_3: SceneStep, steps: HeldSteps, say: Callable[..., None]
) -> None:
    calling(fake_model, say, ("make_starting_picture", {"scene": 2, "note": None}))

    assert steps.held == []
    assert paid_for().count("choose_broll_picture") == 1
    assert results_of("make_starting_picture")[-1].startswith(
        "Scene 2 is already ready for its clip from this line with no note, so nothing was "
        "made or paid for again."
    )


def test_a_picture_step_run_again_after_the_worker_stopped_plans_once(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_photos(True, False)
    needing(NEEDS)
    calling(fake_model, say, ("make_starting_picture", {"scene": 2, "note": None}))
    (step_id,) = steps.held
    # A second answer, were the first wrongly paid for again.
    fake_model.respond("choose_broll_picture", WAY_3_CHOICE, WAY_3_CHOICE)

    def the_worker_stops(*_: object, **__: object) -> None:
        raise WorkerStopped

    # The worker stops once the plan is paid for, as the step is being saved.
    with monkeypatch.context() as stopping:
        stopping.setattr(SceneStep, "save", the_worker_stops)
        with pytest.raises(WorkerStopped):
            steps.run_next()
    fake_model.respond("produce", turn(says="Ready!"))

    tasks.run_scene_step(step_id)

    assert paid_for().count("choose_broll_picture") == 1
    assert SceneStep.objects.get(pk=step_id).motion_prompt == VIDEO_PROMPT


# --- The clip ----------------------------------------------------------------------------------


@pytest.fixture
def ready(fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> None:
    """Scene 2, made way 3, with its pictures picked and its audio transcribed."""
    add_photos(True, False)
    needing(NEEDS)
    made_ready_way_3(fake_model, steps, say)


def made_ready_way_3(fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> None:
    calling(
        fake_model,
        say,
        ("make_starting_picture", {"scene": 2, "note": None}),
        ("make_line_audio", {"scene": 2}),
    )
    fake_model.respond("choose_broll_picture", WAY_3_CHOICE)
    run(fake_model, steps)
    calling(fake_model, say, ("transcribe_line_audio", {"scene": 2}))
    run(fake_model, steps)


def test_the_clip_is_asked_for_from_the_example_pictures_and_no_starting_picture(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    clip_of_scene_2(fake_model, steps, say)

    assert (
        clips_asked("starting_picture"),
        clips_asked("example_pictures"),
        clips_asked("prompt"),
    ) == ([None], [[photo_file(1), photo_file(4)]], [VIDEO_PROMPT])
    clip = ProducedItem.objects.get(kind="clip")
    assert clip.picture is None
    assert clip.step is not None and clip.step.picture_step == SceneStep.objects.get(
        kind="starting_picture"
    )
    assert results_of("make_clip") == [
        "Started scene 2's clip. It isn't made yet: you'll be told when it's ready."
    ]
    assert clip.step.result == (
        f"Background step finished: scene 2's clip is ready (version 1, {clip.seconds:g} "
        "seconds), made from audio version 1. Scene 2 is finished. Tell the shop owner."
    )


def test_the_clip_is_sent_the_pictures_picked_though_the_page_is_read_again_since(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    picked = photo_file(4)
    # The page's photos stored again under new keys, as when the page is read again.
    for each in ProductPhoto.objects.filter(position__in=(1, 4)):
        each.file = file_store.save(f"again-{each.position}.png", photo(each.position))
        each.save()

    clip_of_scene_2(fake_model, steps, say)

    assert clips_asked("example_pictures")[0][1] == picked


def test_the_clip_tool_refuses_while_the_picture_step_is_running(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    add_photos(True, False)
    needing(NEEDS)
    calling(
        fake_model,
        say,
        ("make_line_audio", {"scene": 2}),
    )
    run(fake_model, steps)
    calling(fake_model, say, ("transcribe_line_audio", {"scene": 2}))
    run(fake_model, steps)
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    calling(fake_model, say, ("make_starting_picture", {"scene": 2, "note": None}))
    calling(fake_model, say, ("make_clip", {"scene": 2}))

    assert results_of("make_clip") == [
        "Refused: scene 2 has no starting picture yet. Make its starting picture first. "
        "Nothing was done.",
        "Refused: scene 2's starting picture is still being made. You'll be told when it's "
        "ready; make the clip then. Nothing was done.",
    ]
    assert "make_broll_clip" not in paid_for()


def test_a_clip_already_made_from_the_same_pictures_and_audio_isnt_made_again(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    clip_of_scene_2(fake_model, steps, say)
    calling(fake_model, say, ("make_clip", {"scene": 2}))

    assert steps.held == []
    assert paid_for().count("make_broll_clip") == 1
    assert results_of("make_clip")[-1].startswith(
        "Scene 2's clip was already made for this scene and audio (version 1), so nothing "
        "was made or paid for again."
    )


def test_a_scene_planned_again_with_other_needs_gets_no_clip_until_it_is_picked_again(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    needing([{"what": "the tea's colour", "photos": [3]}])

    clip_of_scene_2(fake_model, steps, say)

    assert results_of("make_clip") == [
        "Refused: scene 2's starting picture was made for an earlier line, or for what the "
        "scene showed before, and the scene has changed since. Make its starting picture "
        "again first. Nothing was done."
    ]


def test_a_clip_step_run_again_after_the_worker_stopped_pays_nothing_twice(
    fake_model: FakeModel,
    ready: None,
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    (step_id,) = steps.held

    def the_worker_stops(*_: object, **__: object) -> None:
        raise WorkerStopped

    with monkeypatch.context() as stopping:
        stopping.setattr(ProducedItem.objects, "create", the_worker_stops)
        with pytest.raises(WorkerStopped):
            steps.run_next()
    fake_model.respond("produce", turn(says="Ready!"))

    tasks.run_scene_step(step_id)

    assert [p for p in paid_for() if p.endswith("_clip") or p.startswith("choose_")] == [
        "choose_broll_picture",
        "make_broll_clip",
        "collect_broll_clip",
    ]
    assert SceneStep.objects.get(pk=step_id).status == "finished"


def test_the_whole_ad_is_made_with_a_way_3_scene(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    add_photos(True, False)
    needing(NEEDS)
    calling(fake_model, say, ("create_music", {"mood": "light upbeat lo-fi"}))
    made_ready(fake_model, steps, say, (1, 3))
    made_ready_way_3(fake_model, steps, say)
    calling(fake_model, say, *[("make_clip", {"scene": scene}) for scene in (1, 2, 3)])
    run(fake_model, steps)
    calling(fake_model, say, ("assemble_ad", {}))

    ad = Job.objects.get().produced.get(kind="finished_ad")
    assert [cut["scene"] for cut in ad.cuts] == [1, 2, 3]
    assert clips_asked("example_pictures") == [[photo_file(1), photo_file(4)]]
    assert list(ProducedItem.objects.filter(kind="starting_picture", scene__number=2)) == []
