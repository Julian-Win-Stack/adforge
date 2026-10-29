"""Cut-and-send (spec #210 points 7, 13, 14, 17): the code step that builds every B-roll
clip's sent list from the library, the plan's scene pictures and piece, and the ad's drawn
place picture: tiers 1-4 cut at 9, the piece cut from its moment, every picture recoloured to
the clip's colour, the record kept and a red notice for anything left out or fallen back.
The picture models are faked at the gateway; ffmpeg cuts the pieces for real from a tiny
video."""

from typing import Any

import pytest

from adforge import file_store
from agents.models import ToolCall
from chat.models import Session
from gateway.fake import FakeModel
from gateway.models import ModelCall
from jobs import cut_and_send
from jobs.library import (
    LookFrame,
    Moment,
    OwnerPart,
    OwnerPhoto,
    PagePhoto,
    PictureLabel,
    ProductOnly,
    Recolour,
    VideoWatch,
    add_to_library,
    keep_sent_list,
    keep_watch,
    library_of,
    sent_list_of,
)
from jobs.models import Job, ProducedItem, ProductPhoto, ProductVideo, Scene, SceneStep
from jobs.planning import ScenePicture, ScenePiece

from .conftest import a_video, photo

pytestmark = pytest.mark.django_db(transaction=True)

RECOLOUR_OK: dict[str, Any] = {
    "changes": "Only the product's colour changed.",
    "colour_match": "The product is the reference's colour.",
    "verdict": "pass",
    "problem": None,
}
RECOLOUR_BAD: dict[str, Any] = {
    "changes": "The background turned grey too.",
    "colour_match": "The product is the reference's colour.",
    "verdict": "fail",
    "problem": "The background changed as well as the product.",
}


def label(kind: str, side: str, colour: str, words: str, alone: str = "yes") -> PictureLabel:
    return PictureLabel.model_validate(
        {"kind": kind, "side": side, "alone": alone, "colour": colour, "label": words}
    )


FRONT = label("side", "front", "black", "The press from the front.")
BACK = label("side", "back", "black", "The press from the back.")
SIDE = label("side", "side", "black", "The press from the side.")
TOP = label("side", "top", "black", "The press from the top.")
STATE = label("state", "front", "black", "The press open, from the front.")
STATE_BACK = label("state", "back", "black", "The press open, from the back.")
IN_USE = label("in-use", "none", "black", "A hand squeezing the press.", alone="no")


def a_job(colour: str = "black") -> Job:
    return Job.objects.create(product_url="https://shop.example/press", product_colour=colour)


def page_photo(job: Job, picture: PictureLabel, *, colour_photo: bool = False) -> int:
    """`picture` as the job's next page photo, in its library: gives its library number."""
    position = job.photos.count() + 1
    file = file_store.save(f"jobs/{job.pk}/photo-{position}.png", photo(position))
    ProductPhoto.objects.create(
        job=job,
        position=position,
        file=file,
        source_url=f"https://shop.example/{position}.png",
        shows_product_colour=colour_photo,
    )
    return add_to_library(job, file, picture, PagePhoto(photo=position)).number


def page_video(job: Job, colour: str, moments: list[tuple[float, float]]) -> ProductVideo:
    """A watched 24-second page video, the product `colour` in it, with `moments`, and its
    look frame in the library."""
    position = job.videos.count() + 1
    file = file_store.save(
        f"jobs/{job.pk}/videos/{position}.mp4", a_video(32, 48, sound=False, seconds=24)
    )
    video = ProductVideo.objects.create(
        job=job, position=position, kind=ProductVideo.Kind.VIDEO, file=file, duration=24
    )
    keep_watch(
        video,
        VideoWatch(
            moments=[
                Moment(number=n, start=start, end=end, happens="It presses.", on_screen="A hand.")
                for n, (start, end) in enumerate(moments, start=1)
            ],
            look_frames=[1.0],
            colour=colour,
            transcript="music only",
        ),
    )
    frame = file_store.save(f"jobs/{job.pk}/frame-{position}.png", photo(100 + position))
    add_to_library(
        job,
        frame,
        label("state", "front", colour, f"The {colour} press, from video {position}."),
        LookFrame(video=position, second=1.0),
    )
    return video


def a_scene(job: Job, number: int = 2) -> Scene:
    return Scene.objects.create(
        job=job, number=number, line="It minces in one squeeze.", shows="A hand presses garlic."
    )


def the_place(job: Job, kind: ProducedItem.Kind = ProducedItem.Kind.PERSON_IN_ROOM) -> str:
    file = file_store.save(f"jobs/{job.pk}/{kind}.png", photo(500))
    # The presenter in the room is drawn from the portrait as it stands (ticket #256).
    person = (
        ProducedItem.objects.create(job=job, kind=ProducedItem.Kind.PORTRAIT, file=file)
        if kind == ProducedItem.Kind.PERSON_IN_ROOM
        else None
    )
    ProducedItem.objects.create(job=job, kind=kind, version=1, file=file, person=person)
    return file


def paid(purpose: str) -> int:
    return ModelCall.objects.filter(purpose=purpose, outcome=ModelCall.Outcome.SUCCEEDED).count()


def problems(job: Job) -> list[str]:
    job.refresh_from_db()
    return [warning["text"] for warning in job.warnings if warning["level"] == "problem"]


def test_the_sent_list_is_the_front_the_pick_the_person_then_the_other_sides_all_recoloured(
    fake_model: FakeModel,
) -> None:
    job = a_job()
    page_photo(job, STATE)
    front = page_photo(job, FRONT)
    back = page_photo(job, BACK)
    side = page_photo(job, SIDE)
    in_use = page_photo(job, IN_USE)
    page_video(job, "red", [(2, 6)])
    person = the_place(job)
    fake_model.respond("check_recolour", *[RECOLOUR_OK] * 4)

    sending = cut_and_send.cut_and_send(
        a_scene(job),
        person_shown=Scene.PersonShown.HAS_FACE,
        picks=[ScenePicture(number=in_use, for_="in-use")],
        pieces=[ScenePiece(video=1, moment=1)],
    )

    library = library_of(job)
    assert sending.colour == "red"
    assert [(p.image, p.tier, p.library_number, p.drawn, p.for_) for p in sending.pictures] == [
        (1, 1, 7, None, None),
        (2, 2, 8, None, "in-use"),
        (3, 3, None, "person_in_room", None),
        (4, 4, 9, None, None),
        (5, 4, 10, None, None),
    ]
    assert [library[n - 1].source.model_dump() for n in (7, 8, 9, 10)] == [
        {"came_from": "recoloured", "of": of, "colour": "red"} for of in (front, in_use, back, side)
    ]
    assert sending.pictures[2].file == person
    assert [p.file for p in sending.pictures[:2]] == [library[6].file, library[7].file]
    assert sending.recolours == [
        Recolour(of=of, colour="red", outcome="done", made=made)
        for of, made in ((front, 7), (in_use, 8), (back, 9), (side, 10))
    ]
    (piece,) = sending.pieces
    assert (piece.number, piece.video, piece.moment, piece.start, piece.end, piece.trim) == (
        1,
        1,
        1,
        2,
        6,
        None,
    )
    assert sending.left_out == []
    assert problems(job) == []


@pytest.mark.parametrize("picks", [3, 8, 11])
def test_a_full_library_always_sends_the_first_8_picks_after_image_1_and_leaves_out_the_rest(
    fake_model: FakeModel, picks: int
) -> None:
    job = a_job()
    page_photo(job, FRONT)
    for number in range(29):
        page_photo(job, (BACK, SIDE, TOP, STATE_BACK)[number % 4])
    named = [
        page_photo(job, label("in-use", "none", "black", f"A hand, take {n}.", alone="no"))
        for n in range(picks)
    ]
    the_place(job)

    sending = cut_and_send.cut_and_send(
        a_scene(job),
        person_shown=Scene.PersonShown.HAS_FACE,
        picks=[ScenePicture(number=number, for_="in-use") for number in named],
        pieces=[],
    )

    sent = min(picks, 8)
    assert len(sending.pictures) == 9
    assert [p.tier for p in sending.pictures[: 1 + sent]] == [1] + [2] * sent
    assert [p.library_number for p in sending.pictures[1 : 1 + sent]] == named[:sent]
    assert [p.library_number for p in sending.left_out if p.tier == 2] == named[sent:]
    sides = [p for p in sending.pictures + sending.left_out if p.tier == 4]
    assert len(sides) == 22  # the 29 others less the 7 states: back, side and top only
    assert {p.label for p in sides} == {BACK.label, SIDE.label, TOP.label}
    assert STATE_BACK.label not in {p.label for p in sending.pictures + sending.left_out}
    assert [p.tier for p in sending.left_out] == sorted(p.tier for p in sending.left_out)
    assert len(sending.pictures) + len(sending.left_out) == 1 + picks + 1 + 22
    (notice,) = problems(job)
    assert all(f'"{p.label}"' in notice for p in sending.left_out)
    assert "Scene 2's clip" in notice
    assert paid("recolour_picture") == 0


def test_a_moment_over_15_seconds_is_cut_to_its_first_15_and_the_record_says_so(
    fake_model: FakeModel,
) -> None:
    job = a_job()
    page_photo(job, FRONT)
    page_video(job, "black", [(2, 22)])
    the_place(job)

    sending = cut_and_send.cut_and_send(
        a_scene(job),
        person_shown=Scene.PersonShown.HAS_FACE,
        picks=[],
        pieces=[ScenePiece(video=1, moment=1)],
    )

    (piece,) = sending.sent_list("A prompt.").pieces
    assert (piece.moment_start, piece.moment_end) == (2, 22)
    assert (piece.start, piece.end, piece.trim) == (2, 17, "moment_over_limit")
    assert [(v.file, v.start, v.end) for v in sending.reference_videos] == [(piece.file, 2, 17)]


def test_two_pieces_over_15_seconds_together_have_the_last_trimmed_and_the_record_says_so(
    fake_model: FakeModel, settings: Any
) -> None:
    settings.BROLL_MOST_PIECES = 2
    job = a_job()
    page_photo(job, FRONT)
    page_video(job, "black", [(1, 11)])
    page_video(job, "black", [(3, 13)])
    the_place(job)

    sending = cut_and_send.cut_and_send(
        a_scene(job),
        person_shown=Scene.PersonShown.HAS_FACE,
        picks=[],
        pieces=[ScenePiece(video=1, moment=1), ScenePiece(video=2, moment=1)],
    )

    assert [(p.number, p.video, p.start, p.end, p.trim) for p in sending.pieces] == [
        (1, 1, 1, 11, None),
        (2, 2, 3, 8, "pieces_over_limit"),
    ]
    assert problems(job) == []


@pytest.mark.parametrize(
    ("first", "second", "colour"),
    [
        pytest.param(("red", (2, 12)), ("black", (2, 6)), "red", id="10 s red, 4 s black"),
        pytest.param(("black", (2, 6)), ("red", (2, 12)), "red", id="4 s black, 10 s red"),
        pytest.param(("black", (2, 7)), ("red", (2, 7)), "black", id="a tie: Video 1's"),
    ],
)
def test_the_clips_colour_is_the_one_with_the_most_seconds_and_its_videos_frame_the_reference(
    fake_model: FakeModel,
    settings: Any,
    first: tuple[str, tuple[float, float]],
    second: tuple[str, tuple[float, float]],
    colour: str,
) -> None:
    settings.BROLL_MOST_PIECES = 2
    job = a_job("blue")
    page_photo(job, label("side", "front", "blue", "The press from the front."))
    page_video(job, first[0], [first[1]])
    page_video(job, second[0], [second[1]])
    the_place(job)
    fake_model.respond("check_recolour", RECOLOUR_OK)

    sending = cut_and_send.cut_and_send(
        a_scene(job),
        person_shown=Scene.PersonShown.HAS_FACE,
        picks=[],
        pieces=[ScenePiece(video=1, moment=1), ScenePiece(video=2, moment=1)],
    )

    assert sending.colour == colour
    library = library_of(job)
    frame = next(
        i
        for i in library
        if i.label == f"The {colour} press, from video {1 if first[0] == colour else 2}."
    )
    edit = ModelCall.objects.get(purpose="recolour_picture")
    assert edit.handoff["pictures"] == [library[0].file, frame.file]
    assert sending.recolours == [Recolour(of=1, colour=colour, outcome="done", made=4)]


def test_a_clip_sent_no_piece_is_the_ads_colour_recoloured_from_a_colour_photo(
    fake_model: FakeModel,
) -> None:
    job = a_job("Sage green")
    front = page_photo(job, FRONT)
    shows_it = page_photo(
        job, label("in-use", "none", "green", "A hand on it.", alone="no"), colour_photo=True
    )
    page_photo(job, label("side", "back", "sage-green", "The press from the back."))
    page_photo(job, label("side", "side", "green", "The press's side."), colour_photo=True)
    the_place(job)
    fake_model.respond("check_recolour", RECOLOUR_OK)

    sending = cut_and_send.cut_and_send(
        a_scene(job),
        person_shown=Scene.PersonShown.HAS_FACE,
        picks=[ScenePicture(number=shows_it, for_="in-use")],
        pieces=[],
    )

    library = library_of(job)
    assert sending.colour == "sage-green"
    edit = ModelCall.objects.get(purpose="recolour_picture")
    assert edit.handoff["pictures"] == [library[front - 1].file, library[shows_it - 1].file]
    # The colour photos show the ad's colour, and the back is in it already.
    assert sending.recolours == [Recolour(of=front, colour="sage-green", outcome="done", made=5)]
    assert [p.library_number for p in sending.pictures] == [5, shows_it, None, 3, 4]


def test_a_recolour_that_keeps_failing_sends_the_original_and_is_in_the_record_and_notice(
    fake_model: FakeModel,
) -> None:
    job = a_job()
    page_photo(job, FRONT)
    page_video(job, "red", [(2, 6)])
    the_place(job)
    fake_model.respond("check_recolour", *[RECOLOUR_BAD] * 3)

    sending = cut_and_send.cut_and_send(
        a_scene(job),
        person_shown=Scene.PersonShown.HAS_FACE,
        picks=[],
        pieces=[ScenePiece(video=1, moment=1)],
    )

    assert (paid("recolour_picture"), paid("check_recolour")) == (3, 3)
    assert sending.recolours == [Recolour(of=1, colour="red", outcome="fell_back", made=None)]
    assert sending.pictures[0].library_number == 1
    assert sending.pictures[0].file == library_of(job)[0].file
    # One red notice, the recolour step's, naming the picture and the scene.
    (notice,) = problems(job)
    assert f'("{FRONT.label}") couldn\'t be made red, for scene 2' in notice


def test_with_no_picture_to_take_the_colour_from_the_pictures_go_in_their_own_and_it_is_said(
    fake_model: FakeModel,
) -> None:
    job = a_job("red")
    page_photo(job, FRONT)
    the_place(job, ProducedItem.Kind.ROOM_ALONE)

    sending = cut_and_send.cut_and_send(
        a_scene(job), person_shown=Scene.PersonShown.NO_FACE, picks=[], pieces=[]
    )

    assert paid("recolour_picture") == 0
    assert sending.recolours == [Recolour(of=1, colour="red", outcome="fell_back", made=None)]
    assert [p.drawn for p in sending.pictures] == [None, "room"]
    (notice,) = problems(job)
    assert "the plan names no colour photo showing the product in red" in notice
    assert f'library item 1 ("{FRONT.label}", black)' in notice


def test_a_scene_with_no_drawn_place_and_a_piece_past_the_limit_is_told_in_red(
    fake_model: FakeModel,
) -> None:
    job = a_job()
    page_photo(job, FRONT)
    page_video(job, "black", [(2, 6)])
    page_video(job, "black", [(2, 6)])

    sending = cut_and_send.cut_and_send(
        a_scene(job),
        person_shown=Scene.PersonShown.HAS_FACE,
        picks=[],
        pieces=[ScenePiece(video=1, moment=1), ScenePiece(video=2, moment=1)],
    )

    assert [p.tier for p in sending.pictures] == [1]
    assert [p.video for p in sending.pieces] == [1]
    piece, place = problems(job)
    assert "no picture of the presenter in the ad's room" in place
    assert "video 2, moment 1 (2 to 6 s), isn't sent" in piece


def test_a_pick_of_a_version_is_its_original_and_each_original_sends_its_product_only_version(
    fake_model: FakeModel,
) -> None:
    job = a_job()
    page_photo(job, label("side", "front", "black", "The press on a shelf.", alone="no"))
    in_use = page_photo(job, IN_USE)
    cut = file_store.save(f"jobs/{job.pk}/cut.png", photo(77))
    add_to_library(job, cut, FRONT, ProductOnly(of=1))
    the_place(job)

    sending = cut_and_send.cut_and_send(
        a_scene(job),
        person_shown=Scene.PersonShown.HAS_FACE,
        picks=[ScenePicture(number=3, for_="state"), ScenePicture(number=in_use, for_="in-use")],
        pieces=[],
    )

    assert [(p.tier, p.library_number, p.file) for p in sending.pictures[:2]] == [
        (1, 3, cut),
        (2, in_use, library_of(job)[1].file),
    ]


def test_a_steps_first_send_takes_its_scenes_plan_and_its_record_is_kept_on_the_clip(
    fake_model: FakeModel,
) -> None:
    session = Session.objects.create()
    job = Job.objects.create(
        session=session,
        product_url="https://shop.example/press",
        product_colour="black",
        person_gender="man",
    )
    page_photo(job, FRONT)
    in_use = page_photo(job, IN_USE)
    page_video(job, "black", [(2, 6)])
    the_place(job)
    scene = a_scene(job)
    scene.scene_pictures = [{"number": in_use, "for": "in-use"}]
    scene.piece = {"video": 1, "moment": 1}
    scene.save()
    call = ToolCall.objects.create(
        session=session, job=job, agent="producer", tool="make_clip", call_id="c0", arguments={}
    )
    step = SceneStep.objects.create(
        scene=scene,
        kind=SceneStep.Kind.CLIP,
        tool_call=call,
        line=scene.line,
        shows=scene.shows,
        person_shown=Scene.PersonShown.HAS_FACE,
        broll_kind=Scene.BrollKind.DOES_A_JOB,
        broll_result="Fresh mince in a bowl.",
    )
    fake_model.respond(
        "write_first_send_prompt",
        {"labels": ["the press from the front", "a hand squeezing it"], "sentence": "It minces."},
    )

    sending = cut_and_send.planned_sending(step)
    sent = cut_and_send.first_sent_list(step, sending)

    assert [(p.library_number, p.drawn, p.for_) for p in sent.pictures] == [
        (1, None, None),
        (in_use, None, "in-use"),
        (None, "person_in_room", None),
    ]
    assert [(p.video, p.moment) for p in sent.pieces] == [(1, 1)]
    assert sent.prompt.splitlines() == [
        "Film this in real-time speed.",
        "Image 1 is the press from the front.",
        "Image 2 is a hand squeezing it.",
        "Image 3 is the man and the room: he is the person in the clip and it is the background.",
        "Video 1 is only for the movement, not the look.",
        "It minces.",
    ]
    asked = ModelCall.objects.get(purpose="write_first_send_prompt").handoff
    assert asked["scene"]["result"] == "Fresh mince in a bowl."
    clip = ProducedItem.objects.create(
        job=job, scene=scene, step=step, kind=ProducedItem.Kind.CLIP, version=1, file="c.mp4"
    )
    keep_sent_list(clip, sent)
    assert sent_list_of(clip) == sent


def test_a_scene_sent_again_tells_each_red_notice_once(fake_model: FakeModel) -> None:
    job = a_job("red")
    page_photo(job, FRONT)
    scene = a_scene(job)

    for _ in range(2):
        cut_and_send.cut_and_send(
            scene, person_shown=Scene.PersonShown.HAS_FACE, picks=[], pieces=[]
        )

    assert len(problems(job)) == 2  # no reference, no place: each once


def test_a_page_with_no_look_photo_sends_the_owners_front_or_says_there_is_no_image_1(
    fake_model: FakeModel,
) -> None:
    job = a_job()
    page_photo(job, IN_USE)
    the_place(job)
    scene = a_scene(job)

    sending = cut_and_send.cut_and_send(
        scene, person_shown=Scene.PersonShown.HAS_FACE, picks=[], pieces=[]
    )

    assert [p.tier for p in sending.pictures] == [3]
    (notice,) = problems(job)
    assert notice.startswith("Scene 2's clip is sent no photo of the product's look as Image 1")

    owners = file_store.save(f"jobs/{job.pk}/owner.png", photo(61))
    add_to_library(job, owners, BACK, OwnerPhoto(original=owners))
    add_to_library(job, owners, FRONT, OwnerPhoto(original=owners))

    sending = cut_and_send.cut_and_send(
        scene, person_shown=Scene.PersonShown.HAS_FACE, picks=[], pieces=[]
    )

    assert [(p.tier, p.library_number) for p in sending.pictures] == [(1, 3), (3, None), (4, 2)]
    assert len(problems(job)) == 1


def test_an_owner_parts_picture_goes_only_to_the_scene_holding_the_part_after_its_picks(
    fake_model: FakeModel,
) -> None:
    job = a_job()
    page_photo(job, FRONT)
    in_use = page_photo(job, IN_USE)
    file = file_store.save(f"jobs/{job.pk}/part.png", photo(62))
    part = add_to_library(job, file, BACK, OwnerPart(part="the brush's back"))
    owner_side = file_store.save(f"jobs/{job.pk}/owner.png", photo(63))
    add_to_library(job, owner_side, SIDE, OwnerPhoto(original=owner_side))
    the_place(job)
    holding = a_scene(job, 2)
    holding.owner_part = {"what": "the brush's back", "source": "invented", "looks": "Flat."}
    holding.owner_part_picture = job.library.get(number=part.number)
    holding.owner_part_drawn = {"part": holding.owner_part, "photos": [], "file": file}
    holding.save()
    other = a_scene(job, 3)

    sent = {
        scene.number: cut_and_send.cut_and_send(
            scene,
            person_shown=Scene.PersonShown.HAS_FACE,
            picks=[ScenePicture(number=in_use, for_="in-use")],
            pieces=[],
        )
        for scene in (holding, other)
    }

    assert [(p.tier, p.library_number) for p in sent[2].pictures] == [
        (1, 1),
        (2, in_use),
        (2, part.number),
        (3, None),
        (4, 4),
    ]
    assert [(p.tier, p.library_number) for p in sent[3].pictures] == [
        (1, 1),
        (2, in_use),
        (3, None),
        (4, 4),
    ]


@pytest.mark.parametrize(
    "piece", [ScenePiece(video=3, moment=1), ScenePiece(video=1, moment=2)], ids=str
)
def test_a_piece_the_ad_doesnt_have_is_refused(fake_model: FakeModel, piece: ScenePiece) -> None:
    job = a_job()
    page_photo(job, FRONT)
    page_video(job, "black", [(2, 6)])

    with pytest.raises(ValueError):
        cut_and_send.cut_and_send(
            a_scene(job), person_shown=Scene.PersonShown.HAS_FACE, picks=[], pieces=[piece]
        )


@pytest.mark.parametrize(
    ("gender", "words"),
    [
        ("man", "The man and the room: he is the person in the clip"),
        ("woman", "The woman and the room: she is the person in the clip"),
        ("", "The presenter and the room: they are the person in the clip"),
    ],
)
def test_the_drawn_picture_is_labelled_as_the_presenter_the_plan_made(
    fake_model: FakeModel, gender: str, words: str
) -> None:
    job = a_job()
    job.person_gender = gender
    job.save()
    page_photo(job, FRONT)
    the_place(job)

    sending = cut_and_send.cut_and_send(
        a_scene(job), person_shown=Scene.PersonShown.HAS_FACE, picks=[], pieces=[]
    )

    assert sending.pictures[1].label == f"{words} and it is the background."


def test_a_piece_whose_video_has_no_look_frame_takes_the_ads_colour_from_a_colour_photo(
    fake_model: FakeModel,
) -> None:
    job = a_job("red")
    page_photo(job, FRONT)
    shows_it = page_photo(
        job, label("in-use", "none", "red", "A hand on it.", alone="no"), colour_photo=True
    )
    video = page_video(job, "red", [(2, 6)])
    job.library.filter(number=3).delete()  # the video's look frame
    assert video.position == 1
    the_place(job)
    fake_model.respond("check_recolour", RECOLOUR_OK)

    sending = cut_and_send.cut_and_send(
        a_scene(job),
        person_shown=Scene.PersonShown.HAS_FACE,
        picks=[],
        pieces=[ScenePiece(video=1, moment=1)],
    )

    edit = ModelCall.objects.get(purpose="recolour_picture")
    assert edit.handoff["pictures"][1] == library_of(job)[shows_it - 1].file
    assert sending.recolours[0].outcome == "done"
