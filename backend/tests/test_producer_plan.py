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
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs.models import Job
from jobs.work import keep_photo

from .conftest import (
    COPIED,
    MUG_FRONT,
    MUG_SIDE,
    NO_FACE,
    PLAN,
    READABLE,
    SHOWCASE,
    a_plan_with,
    broll,
    openai_answer,
    openai_turn,
    paid_for,
    picture,
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
    openai_server: Callable[..., None], link: str, reply: dict[str, Any]
) -> None:
    """The same, through the real OpenAI code talking to a stand-in OpenAI server."""
    openai_server(
        openai_turn("", ("call_1", "read_page", {"link": link, "target_seconds": None})),
        openai_answer(READABLE),
        openai_answer(COPIED),
        openai_answer(NO_FACE),
        openai_answer(NO_FACE),
        openai_turn("", ("call_2", "plan_ad", {})),
        openai_answer(reply),
        openai_turn("Here's the plan."),
    )


def broll_scene(**labels: Any) -> dict[str, Any]:
    """PLAN with a second scene showing the mug while its line is said, labelled as a
    showcase with no face but for `labels`."""
    scene = {"line": "Holds 350 ml.", "shows": "the mug on a shelf", **SHOWCASE, **labels}
    return a_plan_with(scenes=[{"line": "Meet the Stoneware Mug."}, scene])


def talking_scene(**labels: Any) -> dict[str, Any]:
    """PLAN with a second scene the person says to camera, given B-roll `labels`."""
    return a_plan_with(
        scenes=[{"line": "Meet the Stoneware Mug."}, {"line": "Holds 350 ml.", **labels}]
    )


def a_plan_without(field: str) -> dict[str, Any]:
    """PLAN with one of its plan's fields left out altogether."""
    return {**PLAN, "plan": {name: value for name, value in PLAN["plan"].items() if name != field}}


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


def test_a_first_scene_given_a_blank_shows_is_the_person_talking(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    scenes = [
        {"line": "Meet the Stoneware Mug from Kiln & Co.", "shows": " "},
        broll({"line": "Hand-thrown, holds 350 ml.", "shows": "hot tea poured into the mug"}),
    ]
    planning(fake_model, product_page_url, a_plan_with(scenes=scenes))

    say(f"Make an ad for {product_page_url}")

    assert what_each_scene_shows() == [(1, ""), (2, "hot tea poured into the mug")]


def test_the_products_name_is_found_in_a_talking_line_whatever_its_capitals(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    scenes = [{"line": "Meet the stoneware MUG."}, {"line": "Yours for $24.00."}]
    planning(fake_model, product_page_url, a_plan_with(scenes=scenes))

    say(f"Make an ad for {product_page_url}")

    assert list(Job.objects.get().scenes.values_list("line", flat=True)) == [
        "Meet the stoneware MUG.",
        "Yours for $24.00.",
    ]


def test_the_products_colour_and_the_photos_showing_it_are_stored(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    # The planner marks photo 2 alone, so marking the first photo by default can't pass.
    planning(fake_model, product_page_url, a_plan_with(colour_photos=[2]))

    say(f"Make an ad for {product_page_url}")

    job = Job.objects.get()
    assert job.product_colour == "sage green"
    assert [(photo.position, photo.shows_product_colour) for photo in job.photos.all()] == [
        (1, False),
        (2, True),
    ]
    assert "The product's colour: sage green, shown in photos 2." in plan_ad_result()


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


def broll_fields() -> list[tuple[int, str, str, str, str, list[dict[str, Any]]]]:
    """Each stored scene's number and its B-roll kind, person, usage, result and needs."""
    return list(
        Job.objects.get().scenes.values_list(
            "number", "broll_kind", "person_shown", "usage", "result", "needs"
        )
    )


def test_each_broll_scenes_kind_person_usage_result_and_needs_are_stored(
    planned_with_broll: None,
) -> None:
    assert broll_fields() == [
        (1, "", "", "", "", []),
        (
            2,
            "does a job",
            "has face",
            "Pour a hot drink into the mug.",
            "The mug full of steaming tea.",
            [
                {"what": "the mug's handle", "photos": [2]},
                {"what": "the glaze up close", "photos": [1, 2]},
            ],
        ),
        (3, "showcase", "no face", "", "", []),
        (4, "", "", "", "", []),
    ]


def test_a_scene_sending_exactly_five_pictures_is_planned(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    # The main photo, three needed photos and the presenter's portrait.
    needs = [{"what": what, "photos": [1]} for what in ("the handle", "the base", "the rim")]
    scenes = [{"line": "Meet the Stoneware Mug from Kiln & Co."}, {**TEA_POURED, "needs": needs}]
    planning(fake_model, product_page_url, a_plan_with(scenes=scenes))

    say(f"Make an ad for {product_page_url}")

    assert [len(scene_needs) for *_, scene_needs in broll_fields()] == [0, 3]


def test_a_broll_scenes_blank_usage_and_result_are_stored_blank(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    scenes = [
        {"line": "Meet the Stoneware Mug from Kiln & Co."},
        {**ON_THE_SHELF, "usage": "  ", "result": ""},
    ]
    planning(fake_model, product_page_url, a_plan_with(scenes=scenes))

    say(f"Make an ad for {product_page_url}")

    assert broll_fields()[1] == (2, "showcase", "no face", "", "", [])


def test_the_planner_is_told_how_to_plan_each_broll_scene(
    httpserver: HTTPServer,
    openai_server: Callable[..., None],
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    planning_through_openai(openai_server, product_page_url, PLAN)

    say(f"Make an ad for {product_page_url}")

    request = the_plan_request(httpserver)
    instructions = request["instructions"]
    # Test A (#96): with this hint, 0 of 39 B-roll lines took under 4 seconds to say. Any
    # upper number makes the planner squeeze good lines, and "one short sentence" disagrees.
    assert "A B-roll line has at least about 10 words." in instructions
    assert "short sentence" not in instructions
    # The ad's colour (item 43), and the one photo a scene may never need (item 44).
    assert (
        "pick the colour with the most photos where the product is clearly seen; on a tie, "
        "the colour of Photo 1"
    ) in instructions
    assert "Never name a photo that shows the product in another colour" in instructions
    # The two kinds, "showcase" when unsure, and only the presenter's face (items 12, 27).
    assert '"showcase" when unsure' in instructions
    assert "The only person ever shown is the presenter" in instructions
    scene = request["text"]["format"]["schema"]["$defs"]["PlannedScene"]["properties"]
    assert set(scene) >= {"broll_kind", "person_shown", "usage", "result", "needs"}


# --- What the planner is given --------------------------------------------------------------


def the_plan_request(httpserver: HTTPServer) -> dict[str, Any]:
    """The plan's request to the model: after the producer's first turn, the page check, the
    page's copy, the Face notes of its 2 photos and the producer's second turn."""
    _, _, _, _, _, _, plan, _ = [
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


def test_the_planner_is_handed_the_page_text_the_target_the_photo_count_and_the_conversation(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    planning(fake_model, product_page_url, PLAN, target_seconds=12)

    say(f"Make me a 12 second ad for {product_page_url}")

    handoff = ModelCall.objects.get(purpose="plan_ad").handoff
    assert "Hand-thrown, holds 350 ml, dishwasher safe." in handoff["page_text"]
    # The price is only in the declared data: the copy model skips the price in the words.
    assert '"price": "24.00"' in handoff["page_text"]
    # Stock is only in the page's structured data, never in the words a visitor sees.
    assert "InStock" in handoff["page_text"]
    del handoff["page_text"]
    assert handoff == {
        "product_url": product_page_url,
        "target_seconds": 12,
        "photo_count": 2,
        # The conversation so far, the producer's own words too, so an answer is read next to its
        # question.
        "conversation": [
            {"by": "user", "text": f"Make me a 12 second ad for {product_page_url}"},
            {"by": "producer", "text": "Now I'll plan the ad."},
        ],
    }


def test_the_planner_is_told_to_copy_the_products_name_word_for_word_from_a_talking_line(
    httpserver: HTTPServer,
    openai_server: Callable[..., None],
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    # A page's title often has more in it than anyone says, such as "Anker 313 Power Bank
    # (PowerCore 10K)" or "hydro-stars® + big yellow". Given as the name, it's in no line, so
    # the plan is refused and paid for again: 9 of the first 122 plans were.
    planning_through_openai(openai_server, product_page_url, PLAN)

    say(f"Make an ad for {product_page_url}")

    request = the_plan_request(httpserver)
    assert (
        "Give the product's name exactly as the person says it in one of those scenes, copied "
        "word for word from its line: not the page's full title, and nothing the person "
        "doesn't say, such as a part in brackets or a symbol like ® or ™."
    ) in request["instructions"]
    plan = request["text"]["format"]["schema"]["$defs"]["Plan"]
    assert plan["properties"]["product_name"]["description"] == (
        "The product's name copied word for word from a line where the person talks to camera, "
        "exactly as they say it there: not the page's full title."
    )


def test_the_planner_is_shown_every_product_photo_by_its_number(
    httpserver: HTTPServer,
    openai_server: Callable[..., None],
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    planning_through_openai(openai_server, product_page_url, PLAN)

    say(f"Make an ad for {product_page_url}")

    message = the_plan_message(httpserver)
    assert message["role"] == "user"
    handoff, *photos = message["content"]
    labels, images = photos[0::2], photos[1::2]
    assert labels == [
        {"type": "input_text", "text": "Photo 1"},
        {"type": "input_text", "text": "Photo 2"},
    ]
    assert [(image["type"], image["detail"]) for image in images] == [("input_image", "high")] * 2
    # The front photo is sage green and the side one cream, so each label is on its own photo.
    assert [
        (media_type, image_shown.getpixel((0, 0))) for media_type, image_shown in map(shown, images)
    ] == [
        ("image/png", (143, 170, 140)),
        ("image/png", (236, 229, 206)),
    ]
    # The record says which photos were shown by their keys in the file store, not their bytes.
    call = ModelCall.objects.get(purpose="plan_ad")
    job_id = Job.objects.get().pk
    assert call.images == [
        {"label": "Photo 1", "key": f"jobs/{job_id}/photos/1.png"},
        {"label": "Photo 2", "key": f"jobs/{job_id}/photos/2.png"},
    ]
    assert handoff["type"] == "input_text"
    assert json.loads(handoff["text"]) == call.handoff


def test_the_planner_is_shown_each_photo_shrunk_to_fit_2048_pixels_and_the_kept_ones_are_unchanged(
    httpserver: HTTPServer,
    openai_server: Callable[..., None],
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    planning_through_openai(openai_server, product_page_url, PLAN)

    say(f"Make an ad for {product_page_url}")

    _, *photos = the_plan_message(httpserver)["content"]
    # The front photo, 400 x 300, already fits. The side one, 1600 x 1200, is sent as it is:
    # 2,048 px is the most a model looks at, so nothing under it is shrunk.
    assert [shown(image)[1].size for image in photos[1::2]] == [(400, 300), (1600, 1200)]
    kept = Job.objects.get().photos.all()
    assert [file_store.read(photo.file) for photo in kept] == [MUG_FRONT, MUG_SIDE]


# --- A plan that can't be used ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "broken_rule"),
    [
        pytest.param(a_plan_with(scenes=[]), "List should have at least 1 item", id="no scenes"),
        pytest.param(
            a_plan_with(scenes=[{"line": "  "}]),
            "A scene's line can't be empty.",
            id="a scene with nothing to say",
        ),
        pytest.param(
            a_plan_with(
                scenes=[
                    {
                        "line": "Yours for $24.00.",
                        "overlay": "Hand-thrown stoneware mug, only $24.00",
                    }
                ]
            ),
            "An overlay is a few words: 30 characters at most.",
            id="an overlay too long for its band",
        ),
        pytest.param(
            a_plan_with(
                scenes=[
                    broll({"line": "Hot tea, poured.", "shows": "hot tea poured into the mug"}),
                    {"line": "Meet the Stoneware Mug."},
                ]
            ),
            "The first scene is the person talking to camera",
            id="opening on a scene that shows the product",
        ),
        pytest.param(
            a_plan_with(
                scenes=[
                    {"line": "Meet my favourite mug."},
                    broll(
                        {"line": "The Stoneware Mug, poured.", "shows": "tea poured into the mug"}
                    ),
                ]
            ),
            'No scene where the person talks to camera says "Stoneware Mug": at least one must.',
            id="the name said only over the product",
        ),
        pytest.param(
            broll_scene(broll_kind=None),
            'A B-roll scene needs its kind: "does a job" or "showcase".',
            id="a B-roll scene with no kind",
        ),
        pytest.param(
            broll_scene(person_shown=None),
            'A B-roll scene needs who is in it: "no face" or "has face".',
            id="a B-roll scene with no person label",
        ),
        pytest.param(
            broll_scene(broll_kind="looks good"),
            "Input should be 'does a job' or 'showcase'",
            id="a kind that is neither",
        ),
        pytest.param(
            talking_scene(broll_kind="showcase"),
            "A scene where the person talks to camera has no B-roll kind, person, usage, "
            "result or needs.",
            id="a talking scene with a kind",
        ),
        pytest.param(
            talking_scene(person_shown="no face"),
            "A scene where the person talks to camera has no B-roll kind",
            id="a talking scene with a person label",
        ),
        pytest.param(
            talking_scene(needs=[{"what": "the handle", "photos": [1]}]),
            "A scene where the person talks to camera has no B-roll kind",
            id="a talking scene with needs",
        ),
        pytest.param(
            broll_scene(broll_kind="does a job", usage=" ", result="A full mug."),
            'A "does a job" scene needs its usage: how the product is used in it.',
            id="a job done with no usage",
        ),
        pytest.param(
            broll_scene(broll_kind="does a job", usage="Pour tea in.", result=None),
            'A "does a job" scene needs its result: what you can see at its end.',
            id="a job done with no result",
        ),
        pytest.param(
            broll_scene(result="A full mug."),
            'A "showcase" scene has no result.',
            id="a showcase with a result",
        ),
        pytest.param(
            broll_scene(needs=[{"what": "the handle", "photos": [3]}]),
            "There's no photo 3: the job has 2.",
            id="a need naming a photo after the last one",
        ),
        pytest.param(
            broll_scene(needs=[{"what": "the handle", "photos": []}]),
            "List should have at least 1 item",
            id="a need naming no photo",
        ),
        pytest.param(
            broll_scene(needs=[{"what": " ", "photos": [1]}]),
            "This can't be empty.",
            id="a need that says nothing",
        ),
        pytest.param(
            broll_scene(
                person_shown="has face",
                needs=[{"what": f"part {n}", "photos": [1]} for n in range(1, 5)],
            ),
            "Scene 2 would send 6 pictures (the main photo, 4 needed photos and the "
            "presenter's portrait): 5 at most.",
            id="a scene sending six pictures",
        ),
        pytest.param(a_plan_with(product_name=" "), "This can't be empty.", id="no name"),
        pytest.param(a_plan_with(product_colour=" "), "This can't be empty.", id="no colour"),
        pytest.param(
            a_plan_with(person_looks=" "), "This can't be empty.", id="no look for the person"
        ),
        pytest.param(
            a_plan_with(person_voice=""), "This can't be empty.", id="no voice for the person"
        ),
        # The voice and the portrait are each designed from words alone, and each picks a
        # gender at random when the words don't give one. So the plan must say it, as a
        # choice code enforces rather than a word the planner may leave out.
        pytest.param(
            a_plan_without("person_gender"), "Field required", id="no gender for the person"
        ),
        pytest.param(
            a_plan_with(person_gender="adult"),
            "Input should be 'man' or 'woman'",
            id="a gender that is neither",
        ),
        # The starting picture's pose depends on how big the product is: a chair can't be
        # held at chest height, and studs held there are a few pixels.
        pytest.param(
            a_plan_without("product_size"), "Field required", id="no size for the product"
        ),
        pytest.param(
            a_plan_with(product_size="medium"),
            "Input should be 'tiny', 'handheld' or 'large'",
            id="a size that isn't one of the three",
        ),
        pytest.param(
            a_plan_with(colour_photos=[]),
            "List should have at least 1 item",
            id="no photo showing the colour",
        ),
        pytest.param(
            a_plan_with(colour_photos=[1, 3]),
            "There's no photo 3: the job has 2.",
            id="a photo after the last one",
        ),
        pytest.param(
            a_plan_with(colour_photos=[0]),
            "There's no photo 0: the job has 2.",
            id="a photo before the first one",
        ),
        pytest.param(
            a_plan_with(colour_photos=["1"]),
            "Input should be a valid integer",
            id="a photo number given as text",
        ),
        pytest.param(
            {**PLAN, "plan": None},
            'A "plan" decision needs a plan and no question.',
            id="a plan decision with no plan",
        ),
        pytest.param(
            {**PLAN, "decision": "ask", "question": "Which size?"},
            'An "ask" decision needs a question and no plan.',
            id="a question and a plan",
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
    planning_through_openai(openai_server, product_page_url, reply)

    say(f"Make an ad for {product_page_url}")

    result = plan_ad_result()
    assert result.startswith(
        "Failed: a model's answer couldn't be used (gpt-5.6-sol gave an answer for plan_ad "
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
    # The bad plan is still paid for: 1,200 x $4.00/M in + 300 x $20.00/M out.
    plan_call = ModelCall.objects.get(purpose="plan_ad")
    assert (plan_call.outcome, plan_call.cost_usd) == ("failed", Decimal("0.0108"))
    # Nothing is made from a plan that broke the rules.
    assert paid_for() == ["check_page", "copy_page_text", "note_face", "note_face", "plan_ad"]


def test_a_photo_models_cant_read_is_handed_back_saying_why_and_the_planner_isnt_paid(
    fake_model: FakeModel, page_read: str, say: Callable[..., None]
) -> None:
    # A job whose photos were kept before they had to be PNG, JPEG, WebP or GIF has a BMP.
    # The chat no longer keeps one, so it is added by hand.
    job = Job.objects.get()
    bmp = picture(40, 30, (143, 170, 140), "BMP")
    keep_photo(job, 3, bmp, "image/bmp", source_url="https://shop.example/mug.bmp")
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="I couldn't plan it."))

    say("Plan it")

    assert plan_ad_result() == (
        f"Failed: Photo 3 (jobs/{job.pk}/photos/3.bmp) is image/bmp, which models can't read. "
        "Only PNG, JPEG, WebP or GIF pictures can be shown to a model."
    )
    assert paid_for() == ["check_page", "copy_page_text", "note_face", "note_face"]
    assert not job.scenes.exists()
