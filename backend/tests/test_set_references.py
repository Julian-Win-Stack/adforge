"""set_references and the exclude list (spec #247 point 5, Julian Q17 and Q20), driven through
the chat: "use the photo with the lid off for scene 2", "never send the back photo". The
producer sets the scene's references; code still sends Image 1, the drawn place and the
other sides in #210's order with the 9 cap; an excluded item is never sent, whatever tier
would take it; the cut is named in the answer and the owner's red notice stays. The models
are faked at the gateway. The clip step doesn't send through cut-and-send yet (#240), so
these tests ask cut-and-send for the scene's planned send themselves, as #240 will."""

from collections.abc import Callable
from typing import Any

import pytest

from agents.hard_list import cut_answer
from agents.models import ToolCall
from gateway.fake import FakeModel
from jobs import cut_and_send, stale
from jobs.cut_and_send import Excluded
from jobs.library import PictureLabel, ProductOnly, add_to_library, library_of
from jobs.models import Job, Scene, SceneStep

from .conftest import HeldSteps, given_to_the_producer, producer_turns, results_of
from .test_cut_and_send import BACK, IN_USE, SIDE, label, page_photo
from .test_producer_broll import (
    calling,
    checked,  # noqa: F401 (a fixture)
    made_ready,
    run,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("checked")]

# The ad is sage green; its pictures are too, so nothing is recoloured.
GREEN = "sage-green"
LID_OFF = label("state", "front", GREEN, "The mug with its lid off, from the front.")


def green(picture: Any) -> Any:
    return picture.model_copy(update={"colour": GREEN})


@pytest.fixture
def library() -> dict[str, int]:
    """The ad's library past its two page fronts (items 1 and 2): its back, its side and its
    lid off, by name."""
    job = Job.objects.get()
    return {
        "back": page_photo(job, green(BACK)),
        "side": page_photo(job, green(SIDE)),
        "lid off": page_photo(job, LID_OFF),
    }


def set_references(fake_model: FakeModel, say: Callable[..., None], **asked: Any) -> str:
    """The producer calls set_references for scene 2 with `asked`; gives its answer."""
    calling(
        fake_model,
        say,
        ("set_references", {"scene": 2, "pictures": None, "piece": None, "exclude": None, **asked}),
    )
    return results_of("set_references")[-1]


def scene_2() -> Scene:
    return Scene.objects.get(number=2)


def planned_send() -> cut_and_send.Sending:
    """What scene 2's next clip is sent as it stands, as the clip step will ask (#240)."""
    scene = scene_2()
    call = ToolCall.objects.last()
    assert call is not None
    step = SceneStep.objects.create(
        scene=scene,
        kind=SceneStep.Kind.CLIP,
        tool_call=call,
        **SceneStep.snapshot(scene),
    )
    return cut_and_send.planned_sending(step)


def sent(sending: cut_and_send.Sending) -> list[tuple[int, int | None, str | None]]:
    return [(p.tier, p.library_number, p.drawn) for p in sending.pictures]


def problems() -> list[str]:
    job = Job.objects.get()
    return [warning["text"] for warning in job.warnings if warning["level"] == "problem"]


def test_a_picture_set_for_the_scene_goes_in_tier_2_and_an_excluded_side_is_never_sent(
    fake_model: FakeModel, say: Callable[..., None], library: dict[str, int]
) -> None:
    before = problems()

    answer = set_references(
        fake_model,
        say,
        pictures=[{"number": library["lid off"], "for": "state"}],
        exclude=[library["back"]],
    )

    # Setting references sends nothing, so nothing missing is said to the shop owner yet, and
    # nothing is drawn for it.
    assert problems() == before
    assert not Job.objects.get().produced.filter(kind="room_alone").exists()
    scene = scene_2()
    assert scene.scene_pictures == [{"number": library["lid off"], "for": "state"}]
    assert scene.excluded == [library["back"]]
    assert answer.startswith(
        f"Scene 2's references: pictures {library['lid off']} (state); video piece none; "
        f"excluded {library['back']}. Its next clip is sent: Image 1 "
    )
    assert f'Image 2 "{LID_OFF.label}" (library item {library["lid off"]}, a scene picture)' in (
        answer
    )
    assert (
        f'Not sent, as excluded for scene 2: "{BACK.label}" (library item {library["back"]}, '
        "another side of the product)."
    ) in answer

    sending = planned_send()
    assert [(tier, number) for tier, number, drawn in sent(sending) if drawn is None] == [
        (1, 1),
        (2, library["lid off"]),
        (4, library["side"]),
    ]
    # The ad's room wasn't drawn yet: the send draws it, and it is tier 3.
    assert [drawn for _, _, drawn in sent(sending) if drawn is not None] == ["room"]
    assert problems() == before
    assert sending.excluded == [Excluded(library["back"], BACK.label, 4)]
    assert "Not sent, as excluded for scene 2" in cut_answer(2, sending)


def test_an_excluded_item_takes_no_place_under_the_cap_and_the_cut_is_named_with_its_notice(
    fake_model: FakeModel, say: Callable[..., None], library: dict[str, int]
) -> None:
    job = Job.objects.get()
    in_use = [page_photo(job, green(IN_USE)) for _ in range(8)]

    answer = set_references(
        fake_model,
        say,
        pictures=[{"number": number, "for": "in-use"} for number in in_use],
        exclude=[library["back"]],
    )

    # Image 1 and the 8 picks fill the 9: the place and the side are left out, the back
    # photo is excluded, not cut.
    assert "left out of scene 2's clip" in answer
    sending = planned_send()
    assert [number for _, number, _ in sent(sending)] == [1, *in_use]
    assert library["side"] in [p.library_number for p in sending.left_out]
    assert library["back"] not in [p.library_number for p in sending.left_out]
    assert [item.number for item in sending.excluded] == [library["back"]]
    told = cut_answer(2, sending)
    assert f"library item {library['side']}" in told and "Not sent, as excluded" in told
    (notice,) = [text for text in problems() if "left out" in text]
    assert f"library item {library['side']}" in notice
    assert f"library item {library['back']}" not in notice


def test_excluding_the_front_sends_the_next_front_as_image_1(
    fake_model: FakeModel, say: Callable[..., None], library: dict[str, int]
) -> None:
    set_references(fake_model, say, pictures=[], exclude=[1])
    # Item 2 isn't in the ad's colour: it goes recoloured, from the colour photo.

    sending = planned_send()
    assert sending.pictures[0].tier == 1
    assert [(made.of, made.made) for made in sending.recolours] == [
        (2, sending.pictures[0].library_number)
    ]
    assert 1 not in [p.library_number for p in sending.pictures]
    assert [(item.number, item.tier) for item in sending.excluded] == [(1, 1)]


def test_excluding_every_front_is_said_in_the_answer(
    fake_model: FakeModel, say: Callable[..., None], library: dict[str, int]
) -> None:
    before = problems()

    # Every look photo: the two fronts, the back, the side and the lid off.
    answer = set_references(fake_model, say, exclude=[1, 2, *library.values()])

    assert "No photo of the product's front or look is left to send as Image 1" in answer
    assert problems() == before


def a_version_of(original: int, picture: PictureLabel) -> int:
    """A product-only version of library item `original`, labelled `picture`: gives its
    number."""
    job = Job.objects.get()
    item = next(each for each in library_of(job) if each.number == original)
    return add_to_library(job, item.file, picture, ProductOnly(of=original)).number


def test_excluding_a_version_excludes_its_original(
    fake_model: FakeModel, say: Callable[..., None], library: dict[str, int]
) -> None:
    version = a_version_of(library["side"], green(SIDE))

    set_references(fake_model, say, exclude=[version])

    sending = planned_send()
    sent_numbers = [number for _, number, _ in sent(sending)]
    assert library["side"] not in sent_numbers and version not in sent_numbers
    assert [(item.number, item.tier) for item in sending.excluded] == [(library["side"], 4)]


def test_picking_a_version_of_an_excluded_item_is_refused(
    fake_model: FakeModel, say: Callable[..., None], library: dict[str, int]
) -> None:
    version = a_version_of(library["lid off"], LID_OFF)

    answer = set_references(
        fake_model,
        say,
        pictures=[{"number": version, "for": "state"}],
        exclude=[library["lid off"]],
    )

    assert answer.startswith(f"Refused: library item {version} is both a scene picture and")
    assert scene_2().excluded == []


def test_references_are_refused_while_the_scenes_clip_is_being_made(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], library: dict[str, int]
) -> None:
    made_ready(fake_model, steps, say, (2,))
    calling(fake_model, say, ("make_clip", {"scene": 2}))

    answer = set_references(fake_model, say, exclude=[library["side"]])

    assert answer.startswith(
        "Refused: scene 2's clip is still being made. You'll be told when it's ready; set its "
        "references then."
    )
    assert scene_2().excluded == []
    # The clip finishes the scene, which assembles from it as it stands.
    run(fake_model, steps)
    assert scene_2().status == Scene.Status.FINISHED
    assert SceneStep.objects.filter(
        kind="clip", status="finished", **SceneStep.made_for(scene_2())
    ).exists()


def test_the_exclude_list_is_a_set(
    fake_model: FakeModel, say: Callable[..., None], library: dict[str, int]
) -> None:
    set_references(fake_model, say, exclude=[library["side"], library["back"]])

    answer = set_references(fake_model, say, exclude=[library["back"], library["side"]])

    assert "Nothing changed" in answer
    assert scene_2().excluded == sorted([library["side"], library["back"]])


def test_null_keeps_what_the_scene_has(
    fake_model: FakeModel, say: Callable[..., None], library: dict[str, int]
) -> None:
    set_references(fake_model, say, pictures=[{"number": library["lid off"], "for": "state"}])
    set_references(fake_model, say, exclude=[library["side"]])

    scene = scene_2()
    assert scene.scene_pictures == [{"number": library["lid off"], "for": "state"}]
    assert scene.excluded == [library["side"]]


@pytest.mark.parametrize(
    ("asked", "refused"),
    [
        pytest.param(
            {"pictures": [{"number": 40, "for": "state"}]},
            "Refused: There's no picture 40 in the library.",
            id="unknown picture",
        ),
        pytest.param(
            {"exclude": [41]},
            "Refused: There's no picture 41 in the library.",
            id="unknown exclusion",
        ),
        pytest.param(
            {"pictures": [{"number": 5, "for": "state"}, {"number": 5, "for": "content"}]},
            "Refused: library item 5 is named twice: name each once.",
            id="twice",
        ),
        pytest.param(
            {"pictures": [{"number": 5, "for": "state"}], "exclude": [5]},
            "Refused: library item 5 is both a scene picture and excluded",
            id="both",
        ),
        pytest.param(
            {"piece": {"video": 1, "moment": 1}},
            "Refused: Scene 2 names a moment of video 1: that video has no moments.",
            id="no such moment",
        ),
    ],
)
def test_references_the_ad_doesnt_have_are_refused_and_nothing_is_kept(
    fake_model: FakeModel,
    say: Callable[..., None],
    library: dict[str, int],
    asked: dict[str, Any],
    refused: str,
) -> None:
    answer = set_references(fake_model, say, **asked)

    assert answer.startswith(refused)
    scene = scene_2()
    assert (scene.scene_pictures, scene.piece, scene.excluded) == ([], None, [])


def test_a_scene_said_to_camera_has_no_references(
    fake_model: FakeModel, say: Callable[..., None], library: dict[str, int]
) -> None:
    calling(
        fake_model,
        say,
        ("set_references", {"scene": 1, "pictures": None, "piece": None, "exclude": [3]}),
    )

    assert results_of("set_references")[-1].startswith("Refused: scene 1 is said to camera")
    assert Scene.objects.get(number=1).excluded == []


def test_an_exclusion_makes_the_clip_stale_and_undoing_it_makes_the_clip_current_again(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], library: dict[str, int]
) -> None:
    made_ready(fake_model, steps, say, (2,))
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    run(fake_model, steps)
    assert scene_2().status == Scene.Status.FINISHED

    answer = set_references(fake_model, say, exclude=[library["side"]])

    assert "Scene 2's clip is stale now: made for the scene as it was" in answer
    assert scene_2().status == Scene.Status.PLANNED
    # Only the clip is stale: it isn't handed back as already made.
    assert stale.stale_steps(scene_2()) == ["clip"]

    # Back to the references the clip was made from: it is handed back, current again.
    set_references(fake_model, say, exclude=[])
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    assert "was already made" in results_of("make_clip")[-1]
    assert scene_2().status == Scene.Status.FINISHED
    assert Job.objects.get().produced.filter(kind="clip", scene__number=2).count() == 1


def test_the_same_references_again_change_nothing(
    fake_model: FakeModel, say: Callable[..., None], library: dict[str, int]
) -> None:
    set_references(fake_model, say, exclude=[library["side"]])

    answer = set_references(fake_model, say, exclude=[library["side"]])

    assert "Nothing changed: the scene had these references already." in answer


def test_the_producer_is_told_with_the_clip_what_its_cut_left_out(
    fake_model: FakeModel, say: Callable[..., None], steps: HeldSteps, library: dict[str, int]
) -> None:
    job = Job.objects.get()
    in_use = [page_photo(job, green(IN_USE)) for _ in range(8)]
    set_references(
        fake_model, say, pictures=[{"number": number, "for": "in-use"} for number in in_use]
    )
    made_ready(fake_model, steps, say, (2,))

    calling(fake_model, say, ("make_clip", {"scene": 2}))
    run(fake_model, steps)

    # Image 1 and the 8 picks fill the 9: the side is cut, and the clip's answer says so.
    finished = [
        each["text"]
        for turn in range(1, producer_turns() + 1)
        for each in given_to_the_producer(turn)
        if each["kind"] == "step_finished" and "scene 2's clip is ready" in each["text"]
    ]
    assert finished
    assert f"library item {library['side']}" in finished[-1]
