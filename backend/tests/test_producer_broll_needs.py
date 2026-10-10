"""A B-roll scene with needs, driven through the chat: it gets a starting picture like any
other B-roll scene, and the photos of what it needs that the main photo can't show go to
the picture model, each with its one job, never to the video model (graded #12: shop photos
sent to the video model leaked their own scene into the clip; drawn, it passed). The
producer's model and the scene models are faked at the gateway, but the clips are real tiny
videos and ffmpeg runs for real."""

from collections.abc import Callable
from typing import Any

import pytest

from adforge import file_store
from gateway.fake import FakeModel
from gateway.models import ModelCall
from jobs.models import Job, ProductPhoto, Scene, SceneStep

from .conftest import HeldSteps, handoffs, photo, results_of
from .test_producer_broll import (
    calling,
    checked,  # noqa: F401 (a fixture)
    clip_of_scene_2,
    clips_asked,
    instructed,  # noqa: F401 (a fixture)
    made_ready,
    picture_of_scene_2,
)

# Each request commits on its own, as on the real server, and so does the producer's work.
# Every test starts from a chat whose ad is planned with scene 2 showing the mug, has its
# person, and whose lines passed the fact check.
pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("checked")]

# What scene 2 needs that the mug's photo can't show: the tea's colour, which photo 3 (a
# woman pouring it) and photo 4 (the pour alone) both show.
NEEDS = [{"what": "the tea's colour", "photos": [3, 4]}]


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


def test_a_scene_with_needs_gets_a_starting_picture_made_from_its_needs_photos_too(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    add_photos(True, False)
    needing(NEEDS)

    step = picture_of_scene_2(fake_model, steps, say)

    assert step.status == "finished"
    assert step.produced.get().kind == "starting_picture"
    # The need's photo without a face, after the main photo.
    (asked,) = handoffs("make_starting_picture")
    assert asked["pictures"] == [photo_file(1), photo_file(4)]


def test_the_model_writing_the_prompts_is_told_each_pictures_job_the_portrait_last(
    fake_model: FakeModel,
    instructed: dict[str, list[str]],  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    add_photos(True, False)
    needing(NEEDS, person_shown="has face")

    step = picture_of_scene_2(fake_model, steps, say)

    # Shown the photos it may pick from, then the need's, then the portrait.
    assert ModelCall.objects.get(purpose="choose_broll_picture").images == [
        {"label": "Photo 1", "key": photo_file(1)},
        {"label": "Photo 4", "key": photo_file(4)},
        {"label": "The portrait", "key": portrait_file()},
    ]
    (told,) = instructed["choose_broll_picture"]
    assert "then a photo of what the scene needs" in told
    (planned,) = handoffs("choose_broll_picture")
    assert planned["pictures"] == [
        {"image": 1, "job": "the product, only how it looks"},
        {"image": 2, "job": "only how the tea's colour looks"},
        {"image": 3, "job": "the presenter"},
    ]
    assert step.pictures_sent == [
        {"image": 1, "photo": 1, "job": "the product, only how it looks"},
        {"image": 2, "photo": 4, "job": "only how the tea's colour looks"},
        {"image": 3, "portrait": True, "job": "the presenter"},
    ]
    (asked,) = handoffs("make_starting_picture")
    assert asked["pictures"] == [photo_file(1), photo_file(4), portrait_file()]


def test_a_needs_photo_with_a_strangers_face_is_sent_with_the_person_ignored(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    add_photos(True)
    needing([{"what": "the tea's colour", "photos": [3]}])

    picture_of_scene_2(fake_model, steps, say)

    (planned,) = handoffs("choose_broll_picture")
    assert planned["pictures"][1] == {
        "image": 2,
        "job": "only how the tea's colour looks; ignore the person in it",
    }


def test_the_clip_is_made_from_the_starting_picture_and_no_shop_photo(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    add_photos(False)
    needing([{"what": "the tea's colour", "photos": [3]}])
    made_ready(fake_model, steps, say, (2,))

    clip_of_scene_2(fake_model, steps, say)

    picture = Job.objects.get().produced.get(kind="starting_picture", scene__number=2)
    assert (clips_asked("starting_picture"), clips_asked("example_pictures")) == (
        [picture.file],
        [[]],
    )


# A scene whose picture step finished before every B-roll scene got a picture (old example
# pictures) has no picture: its clip waits for a picture made again.


@pytest.fixture
def made_without_a_picture(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    made_ready(fake_model, steps, say, (2,))
    step = SceneStep.objects.get(kind="starting_picture", scene__number=2)
    step.produced.all().delete()
    step.way = SceneStep.Way.FROM_EXAMPLES
    step.save(update_fields=["way"])


@pytest.mark.usefixtures("made_without_a_picture")
def test_a_scene_whose_picture_step_made_no_picture_gets_no_clip(
    fake_model: FakeModel, say: Callable[..., None]
) -> None:
    calling(fake_model, say, ("make_clip", {"scene": 2}))

    (result,) = results_of("make_clip")
    assert "scene 2 has no starting picture yet" in result
    assert clips_asked("starting_picture") == []


@pytest.mark.usefixtures("made_without_a_picture")
def test_a_scene_whose_picture_step_made_no_picture_has_its_picture_made_again(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    step = picture_of_scene_2(fake_model, steps, say)

    assert step.status == "finished"
    assert step.produced.get().kind == "starting_picture"
