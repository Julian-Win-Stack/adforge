"""The fixing agent films again with references (#235, spec #210 point 23, US 26): film_again
names the pictures to send by library number and a piece by moment number, and writes its own
prompt; cut-and-send takes them as its tier 2 (front photo first, then the picks in the agent's
order, the scene's place, the other sides), recolours, caps at the video API's 9 and cuts the
piece; code adds the speed line and one label per picture and video, sends them on the
reference-mode call and keeps the try's sent list on its row and clip. A references field
naming what the library doesn't hold is refused as the tool's answer: no clip, no try. The
agent, the judge, the pictures and the clips are faked at the gateway; ffmpeg is real."""

from collections.abc import Callable
from typing import Any

import pytest

from adforge import file_store
from agents import fixing_agent
from agents.fixing_agent import Failure, fix_scene
from agents.models import ToolCall
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs import work
from jobs.first_send import PIECE_LABEL, ROOM_LABEL, SPEED_LINE
from jobs.library import (
    LookFrame,
    PagePhoto,
    PictureLabel,
    Recolour,
    Recoloured,
    VideoWatch,
    WatchedMoment,
    add_to_library,
    keep_watch,
    library_of,
    number_moments,
    sent_list_of,
)
from jobs.models import (
    AgentFix,
    AgentFixTry,
    Job,
    ProducedItem,
    ProductVideo,
    ReferencePhoto,
    SceneStep,
)

from .conftest import CUT_OUT_OK, LABELLED, HeldSteps, WorkerStopped, picture
from .sent_lists import piece
from .test_fixing_agent import film_again
from .test_producer_auto_fix import clip_of_scene_2, failing
from .test_producer_broll import checked, instructed  # noqa: F401 (fixtures)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.real_ffmpeg]

FAILED = {
    "verdict": "fail",
    "checks": {"motion": "fail"},
    "answers": {"motion": [{"real_motion": {"verdict": "fail", "problem": "The lid floats."}}]},
}
WHITE = PictureLabel(
    kind="side", side="front", alone="yes", colour="white", label="The mug from the front."
)
BLACK = PictureLabel(
    kind="side", side="back", alone="yes", colour="black", label="The black mug from the back."
)
FRAME = PictureLabel(
    kind="in-use", side="none", alone="no", colour="white", label="A hand lifts the mug's lid."
)
PROMPT = "A hand lifts the lid of the mug on the table and sets it down beside it."
# Items 2 and 7, the second for the movement of a hand, and moment 1 of video 1.
REFERENCES = {
    "pictures": [{"number": 2, "used_for": "state"}, {"number": 7, "used_for": "in-use"}],
    "piece": {"video": 1, "moment": 1},
}
# A try after a refused one, sent a white photo.
WHITE_ONLY = {"pictures": [{"number": 3, "used_for": "state"}], "piece": None}


@pytest.fixture
def failed_clip(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> SceneStep:
    """Scene 2's clip, made and judged, its job with a white page video watched into two
    moments and a library of 7: white page photos, item 2 a black one, item 7 a look frame
    of the video."""
    step = clip_of_scene_2(fake_model, steps, say)
    job = step.scene.job
    video = ProductVideo.objects.create(
        job=job,
        position=1,
        kind=ProductVideo.Kind.VIDEO,
        file=file_store.save("page_video_1.mp4", piece(6.0)),
        duration=6.0,
    )
    keep_watch(
        video,
        VideoWatch(
            moments=number_moments(
                [
                    WatchedMoment(
                        start=0.5, end=3.0, happens="A hand lifts the lid.", on_screen="A mug."
                    ),
                    WatchedMoment(
                        start=3.0, end=5.5, happens="Tea is poured.", on_screen="Steam rises."
                    ),
                ]
            ),
            look_frames=[1.0],
            colour="white",
            transcript="music only",
        ),
    )
    # The library holds only these, so their numbers are 1 to 7.
    job.library.all().delete()
    for number in range(1, 7):
        add_to_library(
            job,
            file_store.save(f"photo_{number}.png", picture(30, 40, (200, 200, 10 * number))),
            BLACK if number == 2 else WHITE,
            PagePhoto(photo=number),
        )
    add_to_library(
        job,
        file_store.save("frame_7.png", picture(30, 40, (250, 250, 250))),
        FRAME,
        LookFrame(video=1, second=1.0),
    )
    return step


def fixed(step: SceneStep) -> fixing_agent.Outcome:
    return fix_scene(step, door="judge", failure=Failure(ruling=FAILED), goal=None)


def with_references(references: Any = REFERENCES, **changes: Any) -> tuple[str, dict[str, Any]]:
    return film_again(references=references, video_prompt=PROMPT, **changes)


def item(job: Job, number: int) -> bytes:
    [found] = [each for each in library_of(job) if each.number == number]
    return file_store.read(found.file)


def the_room(job: Job) -> bytes:
    room = work.latest(job, ProducedItem.Kind.ROOM_ALONE)
    assert room is not None
    return file_store.read(room.file)


def test_the_agent_films_again_with_library_items_and_a_moment(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    job = failed_clip.scene.job
    fake_model.respond("fix_scene", turn(calls=[with_references()]))
    asked_before = len(fake_model.broll_clips_asked)

    fixed(failed_clip)

    # Item 2 is black: it is recoloured to the video's white, with its look frame for the
    # colour, and the recoloured picture goes in its place.
    [recoloured] = [each for each in library_of(job) if each.number == 8]
    assert (recoloured.colour, recoloured.source.model_dump()) == (
        "white",
        {"came_from": "recoloured", "of": 2, "colour": "white"},
    )
    [asked] = fake_model.broll_clips_asked[asked_before:]
    assert asked["starting_picture"] is None
    # Cut-and-send's order: the front photo, the picks in the agent's order, then the ad's
    # room (tier 3: the scene shows no face).
    assert asked["example_pictures"] == [item(job, 1), item(job, 8), item(job, 7), the_room(job)]
    [sent_piece] = asked["reference_videos"]
    assert 0.5 <= sent_piece["start"] < sent_piece["end"] <= 3.0
    # The speed line, a label for each picture in the order sent and one for the piece, then
    # the agent's own prompt.
    assert asked["prompt"].split("\n") == [
        SPEED_LINE,
        "Image 1 is the mug from the front.",
        # The recoloured item keeps its label; its line ends on the item's own colour.
        "Image 2 is the black mug from the back, used for how the product is in this scene, "
        "in white.",
        "Image 3 is a hand lifts the mug's lid, used for how the product is held or used.",
        f"Image 4 is {ROOM_LABEL}.",
        f"Video 1 is {PIECE_LABEL}.",
        fixing_agent.RECOLOURED_LINE,
        PROMPT,
    ]
    # The try's clip carries its sent list.
    [row] = AgentFixTry.objects.filter(agent_fix=AgentFix.objects.get())
    assert row.clip is not None
    sent = sent_list_of(row.clip)
    assert sent is not None
    assert [(p.image, p.tier, p.library_number, p.drawn, p.for_) for p in sent.pictures] == [
        (1, 1, 1, None, None),
        (2, 2, 8, None, "state"),
        (3, 2, 7, None, "in-use"),
        (4, 3, None, "room", None),
    ]
    assert sent.colour == "white" and sent.prompt == asked["prompt"]
    assert sent.recolours == [Recolour(of=2, colour="white", outcome="done", made=8)]
    [cut] = sent.pieces
    assert (cut.video, cut.moment, cut.moment_start, cut.moment_end) == (1, 1, 0.5, 3.0)
    assert file_store.read(cut.file) == sent_piece["file"]
    # The agent is told the judge's verdict and shown the clip's frames.
    call = ToolCall.objects.get(tool="film_again")
    assert "The judge's verdict:" in call.result
    assert any("new clip" in each["label"] for each in call.pictures)


@pytest.mark.parametrize(
    ("references", "changes", "refused"),
    [
        (
            {"pictures": [{"number": 99, "used_for": "state"}], "piece": None},
            {},
            "there is no library item 99",
        ),
        (
            {"pictures": [{"number": 2, "used_for": "state"}], "piece": {"video": 1, "moment": 9}},
            {},
            "page video 1 has no moment 9",
        ),
        (
            {"pictures": [{"number": 2, "used_for": "state"}], "piece": {"video": 4, "moment": 1}},
            {},
            "there is no watched page video 4",
        ),
        (
            {"pictures": [{"number": 2, "used_for": "state"}] * 2, "piece": None},
            {},
            "library item 2 is named twice",
        ),
        ({"pictures": [], "piece": {"video": 1, "moment": 1}}, {}, "name at least one picture"),
        (
            {"pictures": [{"number": 1, "used_for": "state"}], "piece": None},
            {},
            "library item 1 is the front photo, which every clip is sent as Image 1",
        ),
    ],
)
def test_references_naming_what_the_library_doesnt_hold_are_refused_with_no_clip_or_try(
    fake_model: FakeModel,
    failed_clip: SceneStep,
    references: dict[str, Any],
    changes: dict[str, Any],
    refused: str,
) -> None:
    fake_model.respond(
        "fix_scene",
        turn(calls=[with_references(references, **changes)]),
        turn(calls=[with_references(WHITE_ONLY)]),
    )
    # The try after it is sent the black side photo too, recoloured.
    asked_before = len(fake_model.broll_clips_asked)

    fixed(failed_clip)

    refused_call, filmed = ToolCall.objects.filter(tool="film_again").order_by("id")
    assert refused_call.result.startswith(f"Refused: {refused}")
    # The refusal spent no try and made no clip: try 1 is the next call's, its one clip.
    assert [(row.number, row.tool_call_id) for row in AgentFixTry.objects.all()] == [(1, filmed.pk)]
    assert len(fake_model.broll_clips_asked) == asked_before + 1
    assert not ModelCall.objects.filter(purpose="recolour_picture", tool_call=refused_call).exists()


def test_more_pictures_than_the_video_api_takes_are_cut_at_9_and_said_in_red(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    job = failed_clip.scene.job
    for number in range(8, 12):
        add_to_library(
            job,
            file_store.save(f"photo_{number}.png", picture(30, 40, (200, 10 * number, 200))),
            WHITE,
            PagePhoto(photo=number),
        )
    # No photo the plan names shows the ad's colour: nothing to take it from.
    job.photos.update(shows_product_colour=False)
    named = [2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    fake_model.respond(
        "fix_scene",
        turn(
            calls=[
                with_references(
                    {
                        "pictures": [{"number": n, "used_for": "content"} for n in named],
                        "piece": None,
                    }
                )
            ]
        ),
    )
    asked_before = len(fake_model.broll_clips_asked)

    fixed(failed_clip)

    [asked] = fake_model.broll_clips_asked[asked_before:]
    # The front photo, then the first 8 picks.
    assert asked["example_pictures"] == [item(job, n) for n in [1, *named[:8]]]
    assert asked["reference_videos"] == []
    row = AgentFixTry.objects.get()
    assert row.clip is not None
    sent = sent_list_of(row.clip)
    assert sent is not None
    # The last two picks and the room (tier 3, after the picks) are past the cap.
    assert [(left.library_number, left.tier) for left in sent.left_out] == [
        (10, 2),
        (11, 2),
        (None, 3),
    ]
    # No piece: the clip is in the ad's colour.
    assert sent.colour == "-".join(job.product_colour.lower().split())
    # With nothing in the library in that colour to take it from, the pictures go as they
    # are, said in red once.
    assert {each.outcome for each in sent.recolours} == {"fell_back"}
    job.refresh_from_db()
    [no_colour] = [w for w in job.warnings if "went in their own colour" in w["text"]]
    assert no_colour["level"] == "problem"
    [warning] = [w for w in job.warnings if "left out" in w["text"]]
    assert warning["level"] == "problem" and "library item 11" in warning["text"]
    assert "3 pictures were left out" in warning["text"]
    assert "Left out: library item 11." in ToolCall.objects.get(tool="film_again").result


def test_a_worker_restart_mid_try_carries_on_with_the_references_named(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    job = failed_clip.scene.job
    fake_model.respond("fix_scene", turn(calls=[with_references()]))
    fake_model.respond("collect_clip", WorkerStopped())
    asked_before = len(fake_model.broll_clips_asked)

    with pytest.raises(WorkerStopped):
        fixed(failed_clip)
    fixed(failed_clip)

    # Asked for once, with the references named, and nothing paid for twice.
    [asked] = fake_model.broll_clips_asked[asked_before:]
    assert asked["example_pictures"] == [item(job, 1), item(job, 8), item(job, 7), the_room(job)]
    assert ModelCall.objects.filter(purpose="recolour_picture").count() == 1
    [row] = AgentFixTry.objects.all()
    assert row.clip is not None
    sent = sent_list_of(row.clip)
    assert sent is not None
    assert [p.library_number for p in sent.pictures] == [1, 8, 7, None] and len(sent.pieces) == 1
    assert ToolCall.objects.filter(tool="film_again").count() == 1


def test_a_try_after_place_edit_is_sent_the_scenes_own_place(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    job = failed_clip.scene.job
    fake_model.respond(
        "fix_scene",
        turn(
            calls=[
                (
                    "place_edit",
                    {"picture": "room", "edit": "Clear the table.", "new_place": None},
                )
            ]
        ),
        turn(calls=[with_references()]),
    )
    asked_before = len(fake_model.broll_clips_asked)

    fixed(failed_clip)

    own = failed_clip.scene.produced.get(kind=ProducedItem.Kind.SCENE_ROOM)
    [asked] = fake_model.broll_clips_asked[asked_before:]
    assert asked["example_pictures"][-1] == file_store.read(own.file) != the_room(job)
    row = AgentFixTry.objects.get()
    assert row.clip is not None
    sent = sent_list_of(row.clip)
    assert sent is not None
    [place] = [p for p in sent.pictures if p.tier == 3]
    assert (place.drawn, place.file) == ("room", own.file)


def test_a_picture_named_with_its_own_recoloured_version_is_refused(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    job = failed_clip.scene.job
    version = add_to_library(
        job,
        file_store.save("recoloured_2.png", picture(30, 40, (240, 240, 240))),
        WHITE,
        Recoloured(of=2, colour="white"),
    )
    named = {
        "pictures": [
            {"number": 2, "used_for": "state"},
            {"number": version.number, "used_for": "result"},
        ],
        "piece": None,
    }
    fake_model.respond(
        "fix_scene", turn(calls=[with_references(named)]), turn(calls=[with_references(WHITE_ONLY)])
    )

    fixed(failed_clip)

    refused_call, filmed = ToolCall.objects.filter(tool="film_again").order_by("id")
    assert refused_call.result.startswith(
        f"Refused: library items 2 and {version.number} are the same picture"
    )
    assert [row.tool_call_id for row in AgentFixTry.objects.all()] == [filmed.pk]


def test_references_with_a_face_after_a_room_only_place_edit_are_refused(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    fake_model.respond(
        "fix_scene",
        turn(
            calls=[
                (
                    "place_edit",
                    {"picture": "room", "edit": "Clear the table.", "new_place": None},
                )
            ]
        ),
        turn(calls=[with_references(person_shown="has face")]),
        turn(calls=[with_references()]),
    )
    asked_before = len(fake_model.broll_clips_asked)

    fixed(failed_clip)

    refused_call, filmed = ToolCall.objects.filter(tool="film_again").order_by("id")
    assert refused_call.result.startswith("Refused: the scene's newest place is its own")
    assert "call place_edit with picture person_in_room first" in refused_call.result
    # No clip and no try for the refused call: the one try is the next call's, in its own room.
    assert [row.tool_call_id for row in AgentFixTry.objects.all()] == [filmed.pk]
    assert len(fake_model.broll_clips_asked) == asked_before + 1


def test_a_reference_the_scene_excludes_is_refused_with_no_clip_or_try(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    # The producer's set_references excluded library item 7 for scene 2 (Julian Q20).
    failed_clip.scene.excluded = [7]
    failed_clip.scene.save(update_fields=["excluded"])
    fake_model.respond("fix_scene", turn(calls=[with_references()]), turn(calls=[film_again()]))
    # The plain try re-cuts the sent list: a picture recoloured to the clip's colour passes.
    asked_before = len(fake_model.broll_clips_asked)

    fixed(failed_clip)

    refused_call, filmed = ToolCall.objects.filter(tool="film_again").order_by("id")
    assert refused_call.result.startswith(
        "Refused: library item 7 is excluded for this scene: it is never sent for it."
    )
    assert refused_call.answer == "cant"
    assert [(row.number, row.tool_call_id) for row in AgentFixTry.objects.all()] == [(1, filmed.pk)]
    assert len(fake_model.broll_clips_asked) == asked_before + 1


# --- A try starts from the newest clip's sent list (#242, N3) --------------------------------


def picks_and_pieces(clip: ProducedItem | None) -> tuple[list[Any], list[Any]]:
    """The tier 2 picks (original library numbers) and the pieces a clip was sent."""
    assert clip is not None
    sent = sent_list_of(clip)
    assert sent is not None
    originals = {each.number: each for each in library_of(clip.job)}

    def original(number: int) -> int:
        source = originals[number].source
        return int(source.of) if isinstance(source, Recoloured) else number

    picks = [
        (original(p.library_number), p.for_)
        for p in sent.pictures
        if p.tier == 2 and p.library_number is not None
    ]
    return picks, [(p.video, p.moment) for p in sent.pieces]


def test_a_try_naming_no_references_is_sent_the_newest_tries_not_the_first_clips(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    # Try 1 names references, try 2 names none, try 3 names others: try 2 is sent try 1's
    # picks and piece, which aren't the failed clip's, and try 3 its own.
    fake_model.respond(
        "fix_scene",
        turn(calls=[with_references()]),
        turn(calls=[film_again(video_prompt="A hand pours tea into the mug on the table.")]),
        turn(calls=[with_references(WHITE_ONLY)]),
        turn(calls=[("give_up", {"reason": "It keeps floating."})]),
    )
    fake_model.respond("check_clip_motion", *[failing("It floats.")] * 3)

    fixed(failed_clip)

    first = failed_clip.produced.filter(kind=ProducedItem.Kind.CLIP).order_by("id").first()
    one, two, three = [row.clip for row in AgentFixTry.objects.order_by("number")]
    assert picks_and_pieces(one) == ([(2, "state"), (7, "in-use")], [(1, 1)])
    assert picks_and_pieces(two) == picks_and_pieces(one) != picks_and_pieces(first)
    assert picks_and_pieces(three) == ([(3, "state")], [])
    assert two is not None and str(sent_list_of(two).prompt).endswith(  # type: ignore[union-attr]
        "A hand pours tea into the mug on the table."
    )


# --- The shop owner's thing goes with every try (#240, #242 review S2) ------------------------


def test_the_shop_owners_thing_is_sent_with_a_try_with_references_and_one_without(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    fake_model.answer_unscripted("check_cut_out", lambda _: CUT_OUT_OK)
    fake_model.answer_unscripted("label_photo", lambda _: {**LABELLED, "colour": "white"})
    job = failed_clip.scene.job
    mug = ReferencePhoto.objects.create(
        scene=failed_clip.scene,
        file=file_store.save("owner-mug.png", picture(60, 80, (20, 20, 20))),
        thing="black mug",
        use=ReferencePhoto.Use.CLIP,
    )
    failed_clip.reference_photos.add(mug)
    fake_model.respond(
        "fix_scene",
        turn(calls=[with_references(WHITE_ONLY)]),
        turn(calls=[film_again(video_prompt="A hand pours tea into the mug on the table.")]),
        turn(calls=[("give_up", {"reason": "It keeps floating."})]),
    )
    fake_model.respond("check_clip_motion", *[failing("It floats.")] * 2)
    before = len(fake_model.broll_clips_asked)

    fixed(failed_clip)

    mug.refresh_from_db()
    assert mug.drawn and mug.drawn != mug.file
    one, two = fake_model.broll_clips_asked[before:]
    for asked in (one, two):
        assert file_store.read(mug.drawn) in asked["example_pictures"]
        assert file_store.read(mug.file) not in asked["example_pictures"]
    # Drawn clean once, for both tries.
    assert ModelCall.objects.filter(purpose="make_reference_photo_picture").count() == 1
    assert job.library.filter(number__gt=7).exists()
