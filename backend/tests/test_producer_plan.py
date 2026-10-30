"""Planning the ad, driven through the chat. The producer's model is faked at the gateway to
read the page and call plan_ad, and each test checks what the tool handed back, what was
stored and what was paid for."""

import base64
import io
import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import PIL.Image
import pytest
from pytest_httpserver import HTTPServer

from adforge import file_store
from gateway.fake import DEFAULT_WHY, FakeModel, meanwhile, turn
from gateway.models import ModelCall
from jobs.library import PagePhoto, PictureLabel, VideoWatch, add_to_library, keep_watch
from jobs.models import Job, ProductVideo
from jobs.planning import PLAN_INSTRUCTIONS, producer_decision_for

from .conftest import (
    COPIED,
    LABELLED,
    MUG_FRONT,
    MUG_SIDE,
    NO_FACE,
    PLAN,
    READABLE,
    a_plan_with,
    broll,
    broll_labels,
    openai_answer,
    openai_turn,
    paid_for,
    results_of,
)

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)


def planning(
    fake_model: FakeModel, link: str, reply: dict[str, Any], *, target_seconds: int | None = None
) -> None:
    """Script the producer to read `link` and plan the ad, with the planner answering `reply`."""
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": link, "target_seconds": target_seconds})]),
        turn(says="Now I'll plan the ad.", calls=[("plan_ad", {})]),
        turn(says="Here's the plan."),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", reply)


def planning_through_openai(
    openai_server: Callable[..., None], link: str, *replies: dict[str, Any]
) -> None:
    """The same, through the real OpenAI code talking to a stand-in OpenAI server: the
    planner answers `replies`, one per ask."""
    openai_server(
        openai_turn(
            "", ("call_1", "read_page", {"link": link, "target_seconds": None, "why": DEFAULT_WHY})
        ),
        openai_answer(READABLE),
        openai_answer(COPIED),
        openai_answer(NO_FACE),
        openai_answer(NO_FACE),
        # Each declared photo is labelled for the library.
        openai_answer(LABELLED),
        openai_answer(LABELLED),
        openai_turn("", ("call_2", "plan_ad", {"why": DEFAULT_WHY})),
        *(openai_answer(reply) for reply in replies),
        openai_turn("Here's the plan."),
    )


def plan_ad_result() -> str:
    (result,) = results_of("plan_ad")
    return result


# --- What is kept from the plan -----------------------------------------------------------


def test_the_plan_and_its_scenes_are_stored_with_the_job(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    planning(fake_model, product_page_url, PLAN)

    say(f"Make an ad for {product_page_url}")

    job = Job.objects.get()
    assert [(scene.number, scene.line, scene.status) for scene in job.scenes.all()] == [
        (1, "Meet the Stoneware Mug from Kiln & Co.", "planned"),
        (2, "Hand-thrown, holds 350 ml, and dishwasher safe.", "planned"),
        (3, "Yours for $24.00.", "planned"),
    ]
    assert (job.product_name, job.product_size) == ("Stoneware Mug", "handheld")
    assert (job.person_gender, job.person_looks, job.person_voice) == (
        "woman",
        "A potter in her thirties in a linen apron, in a sunny workshop.",
        "A warm, relaxed woman in her thirties with a soft British accent.",
    )


def test_the_scripts_format_and_each_scenes_part_are_stored(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    # Creatify's ad generator: a hook, a body in the one structure that suits the product
    # ("Match the structure to your goal"), then a call to action. Each scene keeps its part.
    scenes = [
        {"line": "Meet the Stoneware Mug from Kiln & Co.", "part": "hook"},
        {"line": "Hand-thrown, holds 350 ml, and dishwasher safe.", "part": "hero feature"},
        {"line": "Get yours now for $24.00.", "part": "call to action"},
    ]
    planning(
        fake_model,
        product_page_url,
        a_plan_with(scenes=scenes, script_format="feature cascade"),
    )

    say(f"Make an ad for {product_page_url}")

    job = Job.objects.get()
    assert job.script_format == "feature cascade"
    assert list(job.scenes.values_list("number", "part")) == [
        (1, "hook"),
        (2, "hero feature"),
        (3, "call to action"),
    ]


def test_the_second_way_of_using_the_product_is_stored_with_its_scene(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    # Audit row 11: graded #5 bag, PERFECT as two clips for its two ways, the second clip's
    # start picture an edit of the first's. Code can only do that knowing the second state.
    scenes = [
        {"line": "Meet the Stoneware Mug from Kiln & Co."},
        broll({"line": "Sip hot tea from it on a cold morning.", "shows": "a hand lifts it"}),
        broll(
            {
                "line": "Or fill it with ice and cold brew in summer.",
                "shows": "ice drops into it",
                "second_state": True,
            }
        ),
        {"line": "Yours for $24.00."},
    ]
    planning(fake_model, product_page_url, a_plan_with(scenes=scenes))

    say(f"Make an ad for {product_page_url}")

    assert list(Job.objects.get().scenes.values_list("number", "second_state")) == [
        (1, False),
        (2, False),
        (3, True),
        (4, False),
    ]


def what_each_scene_shows() -> list[tuple[int, str]]:
    """Each stored scene's number and what it shows, in order."""
    return list(Job.objects.get().scenes.values_list("number", "shows"))


@pytest.fixture
def planned_with_tea_poured(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    """A chat whose ad is planned with scene 2 showing hot tea poured into the mug, given
    with spaces around it."""
    scenes = [
        {"line": "Meet the Stoneware Mug from Kiln & Co.", "shows": None},
        broll({"line": "Hand-thrown, holds 350 ml.", "shows": "  hot tea poured into the mug  "}),
        {"line": "Yours for $24.00.", "shows": None},
    ]
    planning(fake_model, product_page_url, a_plan_with(scenes=scenes))
    say(f"Make an ad for {product_page_url}")


def test_a_scene_that_shows_the_product_is_stored_with_what_it_shows(
    planned_with_tea_poured: None,
) -> None:
    assert what_each_scene_shows() == [(1, ""), (2, "hot tea poured into the mug"), (3, "")]


def test_the_producer_is_told_what_each_scene_shows(planned_with_tea_poured: None) -> None:
    assert plan_ad_result().splitlines()[1:5] == [
        "The script:",
        "1. Meet the Stoneware Mug from Kiln & Co.",
        "2. Hand-thrown, holds 350 ml. (Shows, while the voice says it: hot tea poured into "
        "the mug)",
        "3. Yours for $24.00.",
    ]


def test_the_products_name_said_only_over_a_broll_scene_is_found(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    # Every middle scene is B-roll (test ads, 08 Oct: "Yes" to more B-roll), so the name may
    # be said in any line, not only a talking one.
    scenes = [
        {"line": "Meet my favourite mug."},
        broll({"line": "The Stoneware Mug, poured.", "shows": "tea poured into the mug"}),
    ]
    planning(fake_model, product_page_url, a_plan_with(scenes=scenes))

    say(f"Make an ad for {product_page_url}")

    assert Job.objects.get().product_name == "Stoneware Mug"


def test_the_products_colour_and_the_photos_showing_it_are_stored(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    # The planner marks photo 2 alone, so marking the first photo by default can't pass.
    planning(fake_model, product_page_url, a_plan_with(colour_photos=[2]))

    say(f"Make an ad for {product_page_url}")

    job = Job.objects.get()
    assert job.product_colour == "sage-green"
    assert [(photo.position, photo.shows_product_colour) for photo in job.photos.all()] == [
        (1, False),
        (2, True),
    ]
    assert "The product's colour: sage-green, shown in photos 2." in plan_ad_result()


# --- What the plan says about each B-roll scene ----------------------------------------------

# A B-roll scene where the mug does a job you can see, with the presenter's face in it and
# two things the main photo can't show.
TEA_POURED: dict[str, Any] = {
    "line": "Pour in your tea and watch the steam curl up from the glaze.",
    "shows": "hot tea poured into the mug, steam rising",
    "broll_kind": "does a job",
    "person_shown": "has face",
    "usage": "Pour a hot drink into the mug.",
    "result": "The mug full of steaming tea.",
    "needs": [
        {"what": "the mug's handle", "photos": [2]},
        {"what": "the glaze up close", "photos": [1, 2]},
    ],
}

# A B-roll scene showing the mug at its best, with no face in it.
ON_THE_SHELF: dict[str, Any] = {
    "line": "Every one is thrown by hand, so no two mugs are quite the same.",
    "shows": "the mug turning slowly on a sunlit shelf",
    "broll_kind": "showcase",
    "person_shown": "no face",
    "usage": None,
    "result": None,
    "needs": [],
}


@pytest.fixture
def planned_with_broll(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    """A chat whose ad is planned with scene 2 doing a job and scene 3 a showcase."""
    scenes = [
        {"line": "Meet the Stoneware Mug from Kiln & Co."},
        TEA_POURED,
        ON_THE_SHELF,
        {"line": "Yours for $24.00."},
    ]
    planning(fake_model, product_page_url, a_plan_with(scenes=scenes))
    say(f"Make an ad for {product_page_url}")


def test_each_broll_scenes_kind_person_usage_result_and_needs_are_stored(
    planned_with_broll: None,
) -> None:
    assert broll_labels() == [
        ("", "", "", "", []),
        (
            "does a job",
            "has face",
            "Pour a hot drink into the mug.",
            "The mug full of steaming tea.",
            [
                {"what": "the mug's handle", "photos": [2]},
                {"what": "the glaze up close", "photos": [1, 2]},
            ],
        ),
        ("showcase", "no face", "", "", []),
        ("", "", "", "", []),
    ]


# --- What the planner is given --------------------------------------------------------------


def the_plan_request(httpserver: HTTPServer) -> dict[str, Any]:
    """The plan's request to the model: after the producer's first turn, the page check, the
    page's copy, the Face notes and labels of its 2 photos and the producer's second turn."""
    _, _, _, _, _, _, _, _, plan, _ = [
        request.get_json() for request, _ in httpserver.log if request.path == "/v1/responses"
    ]
    return plan  # type: ignore[no-any-return]


def the_plan_message(httpserver: HTTPServer) -> dict[str, Any]:
    """The one message the plan's request sent the model."""
    [message] = the_plan_request(httpserver)["input"]
    return message  # type: ignore[no-any-return]


def shown(image: dict[str, Any]) -> tuple[str, PIL.Image.Image]:
    """The media type and picture an input_image part carries."""
    header, data = image["image_url"].split(",", 1)
    assert header.startswith("data:") and header.endswith(";base64")
    return header.removeprefix("data:").removesuffix(";base64"), PIL.Image.open(
        io.BytesIO(base64.b64decode(data))
    )


def test_the_planner_is_handed_the_page_text_the_target_the_library_count_and_the_conversation(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    planning(fake_model, product_page_url, PLAN, target_seconds=12)

    say(f"Make me a 12 second ad for {product_page_url}")

    handoff = ModelCall.objects.get(purpose="plan_ad").handoff
    assert "Hand-thrown, holds 350 ml, dishwasher safe." in handoff["page_text"]
    # The mug's page has no record of the product, so the planner is given no price: the copy
    # model skips the price in the words, and the declared data's price is taken out.
    assert "24.00" not in handoff["page_text"]
    # Stock is only in the page's structured data, never in the words a visitor sees.
    assert "InStock" in handoff["page_text"]
    del handoff["page_text"]
    assert handoff == {
        # The mug's page has no videos, so no moments.
        "moments": "",
        "library_pictures": 2,
        "pictures_not_shown": 0,
        "product_url": product_page_url,
        "target_seconds": 12,
        # The conversation so far, the producer's own words too, so an answer is read next to its
        # question.
        "conversation": [
            {"by": "user", "text": f"Make me a 12 second ad for {product_page_url}"},
            {"by": "producer", "text": "Now I'll plan the ad."},
        ],
        # The mug's page has no product videos, so none says anything.
        "video_transcripts": [],
    }


def test_the_planner_is_given_the_plan_instructions_and_the_decisions_schema(
    httpserver: HTTPServer,
    openai_server: Callable[..., None],
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    # What the instructions and the schema tell the planner is tried in test_planning.py;
    # this proves the plan's request carries both as they are, each field's description too.
    planning_through_openai(openai_server, product_page_url, PLAN)

    say(f"Make an ad for {product_page_url}")

    request = the_plan_request(httpserver)
    assert request["instructions"] == PLAN_INSTRUCTIONS
    sent = request["text"]["format"]["schema"]["$defs"]
    defined = producer_decision_for(library=(1, 2), moments={}).model_json_schema()["$defs"]
    assert (
        set(sent)
        == set(defined)
        == {
            "Need",
            "OwnerPart",
            "Plan",
            "PlannedScene",
            "ScenePicture",
            "ScenePiece",
        }
    )
    for model in defined:
        assert {
            name: field.get("description") for name, field in sent[model]["properties"].items()
        } == {
            name: field.get("description") for name, field in defined[model]["properties"].items()
        }


def test_the_planner_is_shown_the_library_on_a_sheet_and_the_kept_photos_are_unchanged(
    httpserver: HTTPServer,
    openai_server: Callable[..., None],
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    planning_through_openai(openai_server, product_page_url, PLAN)

    say(f"Make an ad for {product_page_url}")

    message = the_plan_message(httpserver)
    assert message["role"] == "user"
    # The moments first, then the library's sheet, then what the videos say and the page.
    before, label, image, after = message["content"]
    assert label == {
        "type": "input_text",
        "text": "Library pictures 1 to 2 (each numbered and labelled under it)",
    }
    assert (image["type"], image["detail"]) == ("input_image", "high")
    # One sheet, two across: the sage green front photo on the left, the cream side photo on
    # the right, each fitted in its square, its label under it.
    media_type, sheet = shown(image)
    assert media_type == "image/jpeg"
    assert sheet.width == 2 * 600 + 3 * 8
    for x, colour in ((8 + 300, (143, 170, 140)), (16 + 900, (236, 229, 206))):
        pixel = sheet.getpixel((x, 8 + 300))
        assert isinstance(pixel, tuple)
        assert all(abs(a - b) <= 6 for a, b in zip(pixel, colour, strict=True))
    # The record says which sheet was shown by its key in the file store, not its bytes.
    call = ModelCall.objects.get(purpose="plan_ad")
    job = Job.objects.get()
    ((shown_label, key),) = [(image["label"], image["key"]) for image in call.images]
    assert shown_label == label["text"]
    assert key.startswith(f"jobs/{job.pk}/sheets/")
    assert before["type"] == after["type"] == "input_text"
    first, then = json.loads(before["text"]), json.loads(after["text"])
    assert list(first) == ["moments", "library_pictures", "pictures_not_shown"]
    assert list(then)[:2] == ["video_transcripts", "page_text"]
    assert {**first, **then} == call.handoff
    # The photos themselves are kept as they came.
    assert [file_store.read(photo.file) for photo in job.photos.all()] == [MUG_FRONT, MUG_SIDE]


# --- A plan that can't be used ----------------------------------------------------------------


# Every rule a plan can break is tried in test_planning.py. One of pydantic's own and one of
# ours prove the chat hands a broken plan back the same way, whichever check refused it. The
# library's numbers are the one rule test_planning.py can't pin: it hard-codes a library, so
# the chat proves the planner's decision is asked for with the job's own library.
@pytest.mark.parametrize(
    ("reply", "broken_rule"),
    [
        pytest.param(a_plan_with(scenes=[]), "List should have at least 1 item", id="no scenes"),
        pytest.param(
            a_plan_with(
                scenes=[
                    {"line": "Meet my favourite mug."},
                    broll({"line": "The mug, poured.", "shows": "tea poured into the mug"}),
                ]
            ),
            'No line says "Stoneware Mug": at least one must.',
            id="the name said in no line",
        ),
        pytest.param(
            a_plan_with(colour_photos=[1, 3]),
            "There's no picture 3 in the library.",
            id="a picture after the last one",
        ),
    ],
)
def test_a_plan_that_breaks_the_rules_is_handed_back_keeps_nothing_and_is_still_paid_for(
    openai_server: Callable[..., None],
    product_page_url: str,
    say: Callable[..., None],
    reply: dict[str, Any],
    broken_rule: str,
) -> None:
    # Told why and asked again, the planner breaks the same rule again.
    planning_through_openai(openai_server, product_page_url, reply, reply)

    say(f"Make an ad for {product_page_url}")

    result = plan_ad_result()
    assert result.startswith(
        "Failed: a model's answer couldn't be used (gpt-6.1-sol gave an answer for plan_ad "
        "that could not be read:"
    )
    assert broken_rule in result
    job = Job.objects.get()
    assert not job.scenes.exists()
    assert (job.product_colour, job.product_size, job.person_looks, job.person_voice) == (
        "",
        "",
        "",
        "",
    )
    assert job.person_gender == ""
    assert not job.photos.filter(shows_product_colour=True).exists()
    # Both bad plans are still paid for: 1,200 x $2.00/M in + 300 x $10.00/M out each.
    plan_calls = ModelCall.objects.filter(purpose="plan_ad").order_by("id")
    assert [(call.outcome, call.cost_usd) for call in plan_calls] == [
        ("failed", Decimal("0.0054")),
        ("failed", Decimal("0.0054")),
    ]
    # Nothing is made from a plan that broke the rules.
    assert paid_for() == [
        "check_page",
        "copy_page_text",
        "note_face",
        "note_face",
        "label_photo",
        "label_photo",
        "plan_ad",
        "plan_ad",
    ]


# --- Scene pictures by library number and the piece by moment number (#223) -----------------

# Stands in for the library build and the watcher (#220, #221): the job's library holds 12
# pictures, numbered 1 to 12, and its one page video has two moments.
LIBRARY_SIZE = 12
WATCHED_VIDEO: dict[str, Any] = {
    "moments": [
        {
            "number": 1,
            "start": 1.0,
            "end": 6.0,
            "happens": "Tea is poured into the mug.",
            "on_screen": "The mug on a table, a teapot pouring, steam rising.",
        },
        {
            "number": 2,
            "start": 7.0,
            "end": 10.0,
            "happens": "The mug is lifted by its handle.",
            "on_screen": "One hand round the handle, the mug full.",
        },
    ],
    "look_frames": [3.0],
    "colour": "green",
    "transcript": "",
}

# The planner's note on how the mug behaves, one short paragraph.
BEHAVES = (
    "The mug holds a hot drink poured into it and keeps its shape and glaze; it is lifted by "
    "its handle and nothing comes out of it."
)


def the_library_and_its_video_are_ready() -> None:
    """The job's library, filled up to LIBRARY_SIZE after the page's photos, and its watched
    page video, made while the producer works."""
    job = Job.objects.get()
    front = job.photos.get(position=1).file
    for _ in range(job.library.count() + 1, LIBRARY_SIZE + 1):
        add_to_library(
            job,
            front,
            PictureLabel(kind="side", side="front", alone="yes", colour="green", label="The mug."),
            PagePhoto(photo=1),
        )
    video = ProductVideo.objects.create(job=job, position=1, kind=ProductVideo.Kind.VIDEO)
    keep_watch(video, VideoWatch.model_validate(WATCHED_VIDEO))


def planning_from_the_library(
    fake_model: FakeModel, link: str, *replies: dict[str, Any], plans: int = 1
) -> None:
    """Script the producer to read `link`, then, once the library is ready, plan the ad
    `plans` times, the planner answering `replies` in turn."""
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": link, "target_seconds": None})]),
        meanwhile(
            the_library_and_its_video_are_ready,
            turn(says="Now I'll plan the ad.", calls=[("plan_ad", {})]),
        ),
        *(turn(calls=[("plan_ad", {})]) for _ in range(plans - 1)),
        turn(says="Here's the plan."),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", *replies)


TEA_POURED_FROM_THE_VIDEO: dict[str, Any] = {
    **TEA_POURED,
    "needs": [],
    "scene_pictures": [{"number": 4, "for": "in-use"}, {"number": 9, "for": "result"}],
    "piece": {"video": 1, "moment": 1},
}
LIFTED_FULL: dict[str, Any] = {
    **ON_THE_SHELF,
    "shows": "a hand lifts the full mug by its handle",
    "scene_pictures": [{"number": 7, "for": "state"}],
    "piece": None,
}


def a_library_plan(*broll_scenes: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """A plan whose middle scenes are `broll_scenes`, with the behaviour note."""
    scenes = [
        {"line": "Meet the Stoneware Mug from Kiln & Co."},
        *broll_scenes,
        {"line": "Yours for $24.00."},
    ]
    return a_plan_with(scenes=scenes, product_behaviour=BEHAVES, **changes)


def test_each_broll_scene_keeps_its_scene_pictures_and_its_piece_and_the_job_its_behaviour(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    planning_from_the_library(
        fake_model, product_page_url, a_library_plan(TEA_POURED_FROM_THE_VIDEO, LIFTED_FULL)
    )

    say(f"Make an ad for {product_page_url}")

    job = Job.objects.get()
    assert list(job.scenes.values_list("number", "scene_pictures", "piece")) == [
        (1, [], None),
        (
            2,
            [{"number": 4, "for": "in-use"}, {"number": 9, "for": "result"}],
            {"video": 1, "moment": 1},
        ),
        (3, [{"number": 7, "for": "state"}], None),
        (4, [], None),
    ]
    assert job.product_behaviour == BEHAVES


def test_a_scene_naming_twelve_pictures_is_planned_with_all_twelve(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    # The only cap is the video API's 9, applied when the clip is sent: the planner never
    # counts. Its old needs list is no longer capped at 5 pictures either.
    twelve = [{"number": number, "for": "in-use"} for number in range(1, LIBRARY_SIZE + 1)]
    scene = {
        **TEA_POURED_FROM_THE_VIDEO,
        "scene_pictures": twelve,
        "needs": [{"what": f"detail {number}", "photos": [1]} for number in range(1, 6)],
    }
    planning_from_the_library(fake_model, product_page_url, a_library_plan(scene))

    say(f"Make an ad for {product_page_url}")

    (stored,) = Job.objects.get().scenes.filter(number=2).values_list("scene_pictures", flat=True)
    assert stored == twelve


def test_a_plan_naming_a_picture_or_a_moment_the_job_doesnt_have_is_refused(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    beyond = {**TEA_POURED_FROM_THE_VIDEO, "scene_pictures": [{"number": 13, "for": "result"}]}
    no_such_moment = {**TEA_POURED_FROM_THE_VIDEO, "piece": {"video": 1, "moment": 3}}
    planning_from_the_library(
        fake_model, product_page_url, a_library_plan(beyond), a_library_plan(no_such_moment)
    )

    say(f"Make an ad for {product_page_url}")

    # The planner was told the picture it named isn't there and asked again; its second plan
    # named a moment that isn't there, and the producer was handed that.
    no_picture = "Scene 2 names picture 13: the library has no such picture."
    first, again = (asked.instructions for asked in fake_model.asked if asked.purpose == "plan_ad")
    assert no_picture not in first
    assert no_picture in again
    (failed,) = results_of("plan_ad")
    assert "Scene 2 names moment 3 of video 1: that video has moments 1 to 2." in failed
    assert not Job.objects.get().scenes.exists()


def test_a_plan_filming_the_same_action_in_two_scenes_is_refused_and_planned_again(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    poured_again = {
        **TEA_POURED_FROM_THE_VIDEO,
        "line": "Fill it to the brim with tea and it stays light in one hand.",
        "shows": "Hot tea poured into the mug, steam rising.",
    }
    planning_from_the_library(
        fake_model,
        product_page_url,
        a_library_plan(TEA_POURED_FROM_THE_VIDEO, poured_again),
        a_library_plan(TEA_POURED_FROM_THE_VIDEO, LIFTED_FULL),
    )

    say(f"Make an ad for {product_page_url}")

    # Refused, the plan went back to the planner with why, and its next plan stands.
    same_action = "Scenes 2 and 3 film the same action: give each B-roll scene a different one."
    first, again = (asked.instructions for asked in fake_model.asked if asked.purpose == "plan_ad")
    assert same_action not in first
    assert same_action in again
    (planned,) = results_of("plan_ad")
    assert planned.startswith("Planned 4 scenes.")
    assert list(Job.objects.get().scenes.values_list("shows", flat=True)) == [
        "",
        TEA_POURED_FROM_THE_VIDEO["shows"],
        LIFTED_FULL["shows"],
        "",
    ]
