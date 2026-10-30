"""A stranger's face changed to the presenter's before a photo goes to the video model (#275):
a library photo sent whole whose product photo shows a stranger's face is first redrawn by the
picture model with the presenter's portrait, checked for the faces only, and sent as that
FaceSwapped version, made once per photo and portrait. A swap that can't be had keeps the
photo out of the clip, said in red once. From the shop owner's first message to the finished
ad, then each case at cut-and-send.

Every model is faked at the gateway, the shop is served on this machine through the stand-in
Firecrawl, and the fake picture model and video API are read for exactly what they were
asked."""

from collections.abc import Callable
from typing import Any

import pytest

from adforge import file_store
from gateway.fake import FakeModel
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter
from jobs import cut_and_send, face_swap
from jobs.first_send import FIRST_SEND_INSTRUCTIONS
from jobs.library import (
    FaceSwapped,
    LibraryEntry,
    OwnerPhoto,
    PagePhoto,
    PictureLabel,
    ProductOnly,
    Recoloured,
    SentPicture,
    add_to_library,
    library_of,
    sent_list_of,
)
from jobs.models import Job, ProducedItem, ProductPhoto, SceneStep

from .conftest import HeldSteps, handoffs, paid_for, photo
from .test_broll_sent_list_end_to_end import (  # noqa: F401 (fixtures)
    PICKED,
    PRESSED,
    a_plan,
    item_of,
    make_the_ad,
    page_with_a_video,
    watched,
)
from .test_picker_labels import labelled_page  # noqa: F401 (a fixture)
from .test_product_videos import picks_videos  # noqa: F401 (a fixture)

pytestmark = pytest.mark.django_db(transaction=True)

# What the face check answers: every face redrawn, or the stranger's face kept.
FACES_CHANGED: dict[str, Any] = {
    "features": "The face is rounder, the eyes darker and the hair shorter.",
    "verdict": "pass",
    "problem": None,
}


def faces_kept(problem: str) -> dict[str, Any]:
    return {"features": "The same face.", "verdict": "fail", "problem": problem}


# The hand using the shampoo (I104) shows a stranger's face, as the picker notes it.
WITH_A_FACE = {**PICKED, "face_images": [104]}


def a_person_plan(job: Job) -> dict[str, Any]:
    """Scene 2 picks the photo of a hand using the shampoo for how the person is with it."""
    plan = a_plan(item_of(job, "I104"), 1)
    plan["plan"]["scenes"][1]["scene_pictures"] = [
        {"number": item_of(job, "I104"), "for": "person"}
    ]
    return plan


def an_ad(
    fake_model: FakeModel,
    say: Callable[..., None],
    steps: HeldSteps,
    link: str,
    *,
    colour: str = "amber",
    picked: dict[str, Any] = WITH_A_FACE,
) -> None:
    """The shampoo's ad, its scene 2 cut from a page video showing the bottle `colour`."""
    fake_model.answer_unscripted("watch_video", lambda _: watched(colour, PRESSED))
    fake_model.answer_unscripted("check_face_swap", lambda _: FACES_CHANGED)
    make_the_ad(fake_model, say, steps, link, a_person_plan, picked)


def scene_2_sent(job: Job) -> list[SentPicture]:
    (clip,) = job.produced.filter(kind="clip", scene__number=2)
    sent = sent_list_of(clip)
    assert sent is not None
    return sent.pictures


def sent_to_the_video_model(fake_model: FakeModel) -> list[bytes]:
    """Every picture any B-roll clip was sent, as the video API got it."""
    return [
        picture for clip in fake_model.broll_clips_asked for picture in clip["example_pictures"]
    ]


def portrait(job: Job) -> ProducedItem:
    found = job.produced.filter(kind=ProducedItem.Kind.PORTRAIT).order_by("version").last()
    assert found is not None
    return found


def swapped_items(job: Job) -> list[LibraryEntry]:
    return [item for item in library_of(job) if isinstance(item.source, FaceSwapped)]


def warnings_starting(job: Job, opening: str) -> list[str]:
    job.refresh_from_db()
    return [w["text"] for w in job.warnings if w["text"].startswith(opening)]


def clip_step(job: Job, scene: int = 2) -> SceneStep:
    return SceneStep.objects.get(scene__job=job, scene__number=scene, kind=SceneStep.Kind.CLIP)


def test_a_stranger_photo_goes_to_the_clip_with_the_presenters_face(
    fake_model: FakeModel,
    say: Callable[..., None],
    steps: HeldSteps,
    page_with_a_video: str,  # noqa: F811 (the fixture)
) -> None:
    an_ad(fake_model, say, steps, page_with_a_video)

    job = Job.objects.get()
    assert job.produced.filter(kind="finished_ad").exists()
    stranger = library_of(job)[item_of(job, "I104") - 1]
    (swapped,) = swapped_items(job)
    assert swapped.source == FaceSwapped(of=stranger.number, portrait=portrait(job).file)
    assert (swapped.kind, swapped.side, swapped.colour, swapped.label) == (
        stranger.kind,
        stranger.side,
        stranger.colour,
        stranger.label,
    )
    # The clip is sent the swapped picture in the photo's place, never the photo itself.
    (person,) = (picture for picture in scene_2_sent(job) if picture.tier == 2)
    assert (person.library_number, person.file, person.for_) == (
        swapped.number,
        swapped.file,
        "person",
    )
    sent = sent_to_the_video_model(fake_model)
    assert file_store.read(swapped.file) in sent
    assert file_store.read(stranger.file) not in sent
    # The picture model is shown the photo and the portrait; the check, the photo and the
    # new picture only, never the portrait.
    [swap] = handoffs("swap_face")
    assert swap["pictures"] == [stranger.file, portrait(job).file]
    [check] = ModelCall.objects.filter(purpose="check_face_swap")
    assert [image["key"] for image in check.images] == [stranger.file, swapped.file]
    assert paid_for().count("swap_face") == 1

    # The same photo sent again, for another clip of the scene, is the same swap: nothing paid.
    again = cut_and_send.planned_sending(clip_step(job))

    assert swapped.file in [picture.file for picture in again.pictures]
    assert paid_for().count("swap_face") == 1
    assert paid_for().count("check_face_swap") == 1


def test_a_presenter_made_again_gets_a_new_swap_the_next_time_the_photo_is_sent(
    fake_model: FakeModel,
    say: Callable[..., None],
    steps: HeldSteps,
    page_with_a_video: str,  # noqa: F811 (the fixture)
) -> None:
    an_ad(fake_model, say, steps, page_with_a_video)
    job = Job.objects.get()
    (first,) = swapped_items(job)
    remade = ProducedItem.objects.create(
        job=job,
        kind=ProducedItem.Kind.PORTRAIT,
        version=portrait(job).version + 1,
        file=file_store.save(f"jobs/{job.pk}/portrait-2.png", photo(7)),
    )

    sending = cut_and_send.planned_sending(clip_step(job))

    old, new = swapped_items(job)
    assert old == first
    assert new.source == FaceSwapped(of=first.source.of, portrait=remade.file)  # type: ignore[union-attr]
    assert new.file in [picture.file for picture in sending.pictures]
    assert old.file not in [picture.file for picture in sending.pictures]
    assert handoffs("swap_face")[-1]["pictures"][1] == remade.file


def test_a_swap_that_fails_every_check_keeps_the_photo_out_of_the_clip_said_once(
    fake_model: FakeModel,
    say: Callable[..., None],
    steps: HeldSteps,
    page_with_a_video: str,  # noqa: F811 (the fixture)
) -> None:
    fake_model.respond(
        "check_face_swap",
        faces_kept("The eyes are the same."),
        faces_kept("The nose is the same."),
        faces_kept("The whole face is the same."),
    )
    an_ad(fake_model, say, steps, page_with_a_video)

    job = Job.objects.get()
    stranger = library_of(job)[item_of(job, "I104") - 1]
    # The clip is still made, with the rest of its pictures, and the photo is in none of them.
    assert job.produced.filter(kind="finished_ad").exists()
    assert swapped_items(job) == []
    assert file_store.read(stranger.file) not in sent_to_the_video_model(fake_model)
    assert [picture.tier for picture in scene_2_sent(job)] == [1, 3, 4, 4]
    (clip,) = job.produced.filter(kind="clip", scene__number=2)
    sent = sent_list_of(clip)
    assert sent is not None
    [(kept_out)] = sent.strangers_left_out
    assert (kept_out.library_number, kept_out.tier, kept_out.for_) == (
        stranger.number,
        2,
        "person",
    )
    opening = f'Picture {stranger.number} of the ad\'s picture library ("{stranger.label}")'
    [notice] = warnings_starting(job, opening)
    assert "shows a stranger's face" in notice
    assert "isn't sent to the video model for scene 2 or any other scene" in notice
    assert "Try 1: The eyes are the same." in notice
    assert "Try 2: The nose is the same." in notice
    assert "Try 3: The whole face is the same." in notice
    assert "stranger's face" in clip_step(job).result
    paid = paid_for()

    # Sent again, by this scene or another, it says nothing new and pays nothing.
    again = cut_and_send.planned_sending(clip_step(job))

    assert stranger.file not in [picture.file for picture in again.pictures]
    assert [each.library_number for each in again.strangers_left_out] == [stranger.number]
    assert paid_for() == paid
    assert len(warnings_starting(job, opening)) == 1


def test_a_stranger_photo_in_another_colour_is_swapped_then_painted(
    fake_model: FakeModel,
    say: Callable[..., None],
    steps: HeldSteps,
    page_with_a_video: str,  # noqa: F811 (the fixture)
) -> None:
    an_ad(fake_model, say, steps, page_with_a_video, colour="black")

    job = Job.objects.get()
    (swapped,) = swapped_items(job)
    (painted,) = (
        item
        for item in library_of(job)
        if isinstance(item.source, Recoloured) and item.source.of == swapped.number
    )
    (person,) = (picture for picture in scene_2_sent(job) if picture.tier == 2)
    assert (person.library_number, person.file) == (painted.number, painted.file)
    paints = handoffs("recolour_picture")
    assert swapped.file in [paint["pictures"][0] for paint in paints]
    swap_at = paid_for().index("swap_face")
    assert paid_for().index("recolour_picture", swap_at) > swap_at


def test_a_photo_without_a_face_and_a_cut_out_are_never_swapped(
    fake_model: FakeModel,
    say: Callable[..., None],
    steps: HeldSteps,
    page_with_a_video: str,  # noqa: F811 (the fixture)
) -> None:
    # The back stands beside other things and shows a stranger's face: it is sent cut out,
    # the product alone. The hand using the shampoo shows no face.
    an_ad(fake_model, say, steps, page_with_a_video, picked={**PICKED, "face_images": [101]})

    job = Job.objects.get()
    assert job.produced.filter(kind="finished_ad").exists()
    back = item_of(job, "I101")
    assert any(
        isinstance(item.source, ProductOnly) and item.source.of == back for item in library_of(job)
    )
    assert "swap_face" not in paid_for()
    assert "check_face_swap" not in paid_for()
    assert swapped_items(job) == []


def test_the_person_label_says_how_the_person_is_with_the_product() -> None:
    words = "how the person, or their hand, is with the product"
    assert cut_and_send.FOR_WORDS["person"] == words
    assert f'"person" {words}' in " ".join(FIRST_SEND_INSTRUCTIONS.split())
    assert "who does it" not in FIRST_SEND_INSTRUCTIONS


# At the face-swap step itself: a library of a page photo with a stranger's face, a page
# photo without one and an owner's photo as sent with one, and the presenter's portrait.
PERSON = PictureLabel(
    kind="in-use", side="none", alone="no", colour="amber", label="A woman holds the jar."
)


def a_library(*, with_portrait: bool = True) -> Job:
    """Items 1 (a stranger's face) and 2 (none): page photos 1 and 2; item 3: an owner's photo
    with a stranger's face, as it was sent; item 4: a clean picture of item 3's photo."""
    job = Job.objects.create(product_url="https://shop.example/jar")
    for position, has_face in ((1, True), (2, False)):
        file = file_store.save(f"jobs/{job.pk}/photo-{position}.png", photo(position))
        ProductPhoto.objects.create(job=job, position=position, file=file, has_face=has_face)
        add_to_library(job, file, PERSON, PagePhoto(photo=position))
    owners = file_store.save(f"jobs/{job.pk}/owner.png", photo(3))
    ProductPhoto.objects.create(job=job, position=3, file=owners, has_face=True)
    add_to_library(job, owners, PERSON, OwnerPhoto(original=owners))
    clean = file_store.save(f"jobs/{job.pk}/owner-clean.png", photo(4))
    add_to_library(job, clean, PERSON, OwnerPhoto(original=owners))
    if with_portrait:
        ProducedItem.objects.create(
            job=job,
            kind=ProducedItem.Kind.PORTRAIT,
            file=file_store.save(f"jobs/{job.pk}/portrait.png", photo(9)),
        )
    return job


def item(job: Job, number: int) -> LibraryEntry:
    return library_of(job)[number - 1]


def test_a_page_photo_or_an_owners_photo_with_a_face_is_swapped_and_others_are_not(
    fake_model: FakeModel,
) -> None:
    job = a_library()
    fake_model.answer_unscripted("check_face_swap", lambda _: FACES_CHANGED)

    page = face_swap.for_clip(job, item(job, 1), scene=2)
    owners = face_swap.for_clip(job, item(job, 3), scene=2)

    assert page is not None and page.source == FaceSwapped(of=1, portrait=portrait(job).file)
    assert owners is not None and owners.source == FaceSwapped(of=3, portrait=portrait(job).file)
    assert face_swap.for_clip(job, item(job, 2), scene=2) == item(job, 2)
    assert face_swap.for_clip(job, item(job, 4), scene=2) == item(job, 4)
    assert face_swap.for_clip(job, page, scene=2) == page
    assert paid_for() == ["swap_face", "check_face_swap", "swap_face", "check_face_swap"]
    # Each prompt says what kind of photo picture 1 is.
    page_swap, owner_swap = handoffs("swap_face")
    assert "a photo from a shop's page" in page_swap["prompt"]
    assert "a product photo" in owner_swap["prompt"]


def test_a_check_that_fails_twice_then_passes_sends_the_third_picture(
    fake_model: FakeModel,
) -> None:
    job = a_library()
    fake_model.respond(
        "check_face_swap", faces_kept("Same eyes."), faces_kept("Same lips."), FACES_CHANGED
    )

    made = face_swap.for_clip(job, item(job, 1), scene=2)

    tries = [call.output["file"] for call in ModelCall.objects.filter(purpose="swap_face")]  # type: ignore[index]
    assert made is not None and made.file == tries[2]
    assert warnings_starting(job, "Picture 1") == []


def test_with_no_presenter_yet_the_photo_is_kept_out_and_nothing_is_paid(
    fake_model: FakeModel,
) -> None:
    job = a_library(with_portrait=False)

    assert face_swap.for_clip(job, item(job, 1), scene=2) is None
    assert face_swap.for_clip(job, item(job, 1), scene=3) is None

    assert paid_for() == []
    [notice] = warnings_starting(job, "Picture 1 of the ad's picture library")
    assert "no presenter yet" in notice and "scene 2" in notice


def test_a_swap_the_picture_model_refuses_twice_keeps_the_photo_out(
    fake_model: FakeModel,
) -> None:
    job = a_library()
    fake_model.respond("swap_face", BlockedBySafetyFilter("no"), BlockedBySafetyFilter("no"))

    assert face_swap.for_clip(job, item(job, 1), scene=2) is None

    assert fake_model.edits == 0
    [notice] = warnings_starting(job, "Picture 1 of the ad's picture library")
    assert "Try 1: the picture model's safety filter refused it (no)" in notice
    assert "Try 2: the picture model's safety filter refused it (no)" in notice


def test_a_check_that_cant_be_made_fails_the_try_saying_why(fake_model: FakeModel) -> None:
    job = a_library()
    fake_model.respond("check_face_swap", *[BlockedBySafetyFilter("no")] * 3)

    assert face_swap.for_clip(job, item(job, 1), scene=2) is None

    [notice] = warnings_starting(job, "Picture 1 of the ad's picture library")
    assert notice.count("the check's safety filter refused the pictures") == 3


def test_run_again_after_a_worker_stopped_mid_swap_it_pays_for_no_try_twice(
    fake_model: FakeModel,
) -> None:
    job = a_library()
    fake_model.respond("check_face_swap", faces_kept("Same eyes."), RuntimeError("stopped"))
    with pytest.raises(RuntimeError):
        face_swap.for_clip(job, item(job, 1), scene=2)
    fake_model.respond("check_face_swap", FACES_CHANGED)

    made = face_swap.for_clip(job, item(job, 1), scene=2)

    assert made is not None
    assert fake_model.edits == 2
    tries = [call.output["file"] for call in ModelCall.objects.filter(purpose="swap_face")]  # type: ignore[index]
    assert made.file == tries[1]
    assert fake_model.left("check_face_swap") == 0
