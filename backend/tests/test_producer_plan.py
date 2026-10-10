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
    FINISHING_STEP,
    MUG_FRONT,
    MUG_SIDE,
    NO_FACE,
    PLAN,
    READABLE,
    SHOWCASE,
    a_plan_with,
    broll,
    broll_labels,
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


def test_the_products_name_is_found_in_a_line_whatever_its_capitals(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    scenes = [{"line": "Meet the stoneware MUG."}, {"line": "Yours for $24.00."}]
    planning(fake_model, product_page_url, a_plan_with(scenes=scenes))

    say(f"Make an ad for {product_page_url}")

    assert list(Job.objects.get().scenes.values_list("line", flat=True)) == [
        "Meet the stoneware MUG.",
        "Yours for $24.00.",
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


def test_a_scene_sending_exactly_five_pictures_is_planned(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    # The main photo, three needed photos and the presenter's portrait.
    needs = [{"what": what, "photos": [1]} for what in ("the handle", "the base", "the rim")]
    scenes = [{"line": "Meet the Stoneware Mug from Kiln & Co."}, {**TEA_POURED, "needs": needs}]
    planning(fake_model, product_page_url, a_plan_with(scenes=scenes))

    say(f"Make an ad for {product_page_url}")

    assert [len(scene_needs) for *_, scene_needs in broll_labels()] == [0, 3]


def test_a_broll_scenes_blank_usage_and_result_are_stored_blank(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    scenes = [
        {"line": "Meet the Stoneware Mug from Kiln & Co."},
        {**ON_THE_SHELF, "usage": "  ", "result": ""},
    ]
    planning(fake_model, product_page_url, a_plan_with(scenes=scenes))

    say(f"Make an ad for {product_page_url}")

    assert broll_labels()[1] == ("showcase", "no face", "", "", [])


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
    # Test A in docs/plans/broll-boreal-h3.md: with this hint, 0 of 39 B-roll lines took
    # under 4 seconds to say. Any upper number makes the planner squeeze good lines, and "one
    # short sentence" disagrees.
    assert "A B-roll line has at least about 10 words." in instructions
    assert "short sentence" not in instructions
    # One action per B-roll scene: graded #8 toilet (4 steps in one clip) and #5
    # bag (strap on and off in one clip) failed; one step, and one scene per way of use, were
    # graded perfect.
    assert "A B-roll scene films one action" in instructions
    # One movement, no exceptions (Julian, 19:11): N5's clip jumped where Boreal skipped the
    # middle of three steps that flowed into each other.
    assert "even for steps that flow into each other" in instructions
    assert "When a claim needs two end states, give each its own B-roll scene" in instructions
    # Audit row 11: the second state's clip can be made from the first's once code knows it.
    assert "mark the second as the second state" in instructions
    # Test ads (08 Oct): the toilet's "360-degree coverage under the rim" was only said in a
    # talking scene, never shown (point 3b).
    assert "name the other steps" not in instructions
    # Left after the rule above (free check, 08 Oct): a strap clipped on then the bag carried
    # (#5), straps slipped on then a walk (N4).
    assert "attaching, fitting or adjusting it is a movement of its own" in instructions
    # Test ads (08 Oct), N5 Le Duo: the turn was split off the glide ("already clamped and
    # rotated"), so it filmed the page's straightening how-to under a line about curls.
    assert "or turning it" not in instructions
    assert (
        "Still pick only ONE action. Write that one action the way the page words it. If the "
        "page says something happens while doing it, keep that part."
    ) in instructions
    assert 'A part the page says happens while doing it stays, joined by "while".' in instructions
    # Test ads (08 Oct), #8 toilet: the planner wrote "the camera pushes toward" itself, a
    # pointless zoom (Julian 23:57: "a hand or the product, never the camera").
    assert "The one verb is what a hand or the product does, never a camera move" in instructions
    # Test ads (08 Oct), #12 phone case: turned, dropped and rotated from start pictures of
    # one side, the clips drew the back on both sides.
    assert "A hand never turns, flips or spins the product" in instructions
    assert (
        "the side facing the camera when the scene starts faces it when the scene ends, even "
        "after a drop or fall"
    ) in instructions
    assert "give that side its own B-roll scene that starts on it" in instructions
    assert "with the product already set up that way" in instructions
    # Round 1 (08 Oct): N5 still wrote "twists and glides". One verb is checkable.
    assert "with one verb for what moves" in instructions
    # Two end states are two clips with a cut (graded #5 bag, PERFECT); #12's drop ended on
    # the phone face down, its unharmed screen never seen, and turning it over drew the back
    # on both sides (test ads, 08 Oct).
    assert "next B-roll scene starts on the other side, never turning it over" in instructions
    assert "turned over to show" not in instructions
    # The ad's colour, and the one photo a scene may never need: items 43 and 44 of
    # docs/broll-picture-logic.md.
    assert (
        "pick the colour with the most photos where the product is clearly seen; on a tie, "
        "the colour of Photo 1"
    ) in instructions
    assert "Never name a photo that shows the product in another colour" in instructions
    # Only the presenter's face: item 27. "Showcase when unsure" made filler B-roll of a hand
    # holding or setting down the product, and every one failed (round 2, #12 s2, #8 s4, N3).
    assert '"showcase" when unsure' not in instructions
    assert "The only person ever shown is the presenter" in instructions
    scene = request["text"]["format"]["schema"]["$defs"]["PlannedScene"]["properties"]
    assert set(scene) >= {"broll_kind", "person_shown", "usage", "result", "needs"}


def test_the_planner_is_told_the_shape_of_every_script(
    httpserver: HTTPServer,
    openai_server: Callable[..., None],
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    planning_through_openai(openai_server, product_page_url, PLAN)

    say(f"Make an ad for {product_page_url}")

    request = the_plan_request(httpserver)
    instructions = request["instructions"]
    # Creatify's own words, research/creatify-script-shape-originals.md in the project files:
    # "The first 3 seconds determine whether someone watches your ad.", "Match the structure
    # to your goal", "Pick one. Don't mix frameworks in the same video.", "One ad, one idea."
    # and "'Learn more' is not a CTA."
    assert "the hook, the body and the call to action" in instructions
    assert "not the product's name" in instructions
    assert "choose the one format that suits the product" in instructions
    for script_format, parts in [
        ("problem, agitate, solve", "the problem, agitate, solve"),
        ("feature cascade", "the hero feature, a supporting feature, proof"),
        ("before and after", "the before state, the transformation moment, the after state"),
        ("day in the life", "the routine, the key moment, the result"),
    ]:
        assert f'- "{script_format}": {parts}' in instructions
    assert "The ad is about one idea: the product's main benefit." in instructions
    assert "never make one up" in instructions
    assert "get it now" in instructions
    assert 'never "learn more"' in instructions
    schema = request["text"]["format"]["schema"]["$defs"]
    assert "part" in schema["PlannedScene"]["properties"]
    assert "script_format" in schema["Plan"]["properties"]


def test_the_planner_is_told_every_middle_scene_is_broll_that_proves_its_line(
    httpserver: HTTPServer,
    openai_server: Callable[..., None],
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    # Test ads (08 Oct): 7 talking scenes in the middle of 5 ads. Julian 23:45 "Yes": scene 1
    # talking, every middle scene a B-roll. Round 2 (09 Oct): middles with nothing to prove
    # were filled with the product held or set down, and all failed. Julian 04:27: "it must
    # ... prove something about the product ... If we don't have enough data to do that, ask
    # from the shop owner. And if the shop owner cannot provide it, fall back to the talking
    # scene."
    planning_through_openai(openai_server, product_page_url, PLAN)

    say(f"Make an ad for {product_page_url}")

    instructions = the_plan_request(httpserver)["instructions"]
    assert "Most scenes are the person talking to camera" not in instructions
    assert "The first scene is always the person talking to camera." in instructions
    assert (
        "The last scene is the person talking to camera, or a B-roll scene that proves its "
        "line. Every scene between them is a B-roll scene that proves its line"
    ) in instructions
    assert "never the person talking" not in instructions
    assert "a B-roll scene of the product at its best" not in instructions
    assert (
        "When no B-roll kind could prove a middle scene's line, ask the shop owner for what "
        "would show it, as below; if they can't give it, that scene is the person talking to "
        "camera, never a B-roll of the product only held, placed, set down, stood up or "
        "pointed at."
    ) in instructions
    assert (
        'A line that can\'t be filmed, such as "no bleach", is still kept: the voice says it '
        "while the scene proves another claim the page states, never acting the claim out, "
        "or, when no scene could, the person says it to camera."
    ) in instructions
    assert (
        "A claim about something the product, or a part of it, does that a camera could see "
        "is a B-roll scene that shows it."
    ) in instructions
    # No share of B-roll per kind of product: our own research, not Creatify's.
    assert "%" not in instructions
    assert "never skin before and after" in instructions


def test_the_planner_asks_the_shop_owner_when_no_broll_could_prove_a_line(
    httpserver: HTTPServer,
    openai_server: Callable[..., None],
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    # Round 2 fixes 1 and 2 (Julian 04:27, 21:40 "we want [to merge] fix one and fix two"):
    # prove it, else ask the shop owner, else the person says it to camera. N3's thin film,
    # refused, became a filler B-roll ("over another B-roll"): now it is said to camera.
    planning_through_openai(openai_server, product_page_url, PLAN)

    say(f"Make an ad for {product_page_url}")

    instructions = the_plan_request(httpserver)["instructions"]
    assert "Ask because of a scene that shows the product in only four cases" in instructions
    assert (
        "- A middle scene's line is one a camera could film, but no B-roll kind could prove "
        "it: nothing the page, the photos or the shop owner give shows it, and only a hand "
        "holding, placing, setting down, standing up or pointing at the product would be left. "
        'A line a camera can\'t film, such as "no bleach", is never asked about: it follows '
        "the rule for a line that can't be filmed."
    ) in instructions
    # Review P3 7: "at its best" means nothing since the showcase change.
    assert "Ask even if you could plan it only showing the product:" in instructions
    assert "only at its best" not in instructions
    assert "for a line nothing proves, of what would prove it" in instructions
    assert (
        "For a line nothing proves: if they go ahead without one, the person says that line "
        "to camera."
    ) in instructions
    assert "the voice says the claim over another B-roll." not in instructions
    assert (
        "the voice says the claim over another B-roll that proves its own line, or, when none "
        "does, the person says it to camera."
    ) in instructions


def test_the_planner_is_told_a_showcase_only_when_using_it_is_the_proof(
    httpserver: HTTPServer,
    openai_server: Callable[..., None],
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    # Round 2: every "showcase" (a hand holding, setting down or standing up the product)
    # failed: #12 s2, #8 s4, N3 s2-5. Julian 02:15: "The B-roll must do something. It must
    # sell the product." The bag carried as a clutch and worn crossbody was graded PERFECT
    # (#5, decisions/2026-10-07-broll-prompts.md), so wearing it the way the line claims stays.
    planning_through_openai(openai_server, product_page_url, PLAN)

    say(f"Make an ad for {product_page_url}")

    request = the_plan_request(httpserver)
    instructions = request["instructions"]
    assert '"showcase" for everything else' not in instructions
    assert (
        '"showcase" only when using or wearing the product the way the line claims is itself '
        "the proof, such as a bag carried as a clutch or worn crossbody hands-free. A hand only "
        "holding, placing, setting down, standing up or pointing at the product is never a "
        "B-roll scene."
    ) in instructions
    assert "When no kind fits, the scene is not B-roll." in instructions
    # Review P3 5: a problem scene is a hand or the product beside the problem; the problem
    # in plain view is what proves its line, so the ban above doesn't cover it.
    assert (
        'In a "shows the problem" scene, the problem in plain view is what proves its line.'
    ) in instructions
    kind = request["text"]["format"]["schema"]["$defs"]["PlannedScene"]["properties"]["broll_kind"]
    assert '"showcase" when unsure' not in kind["description"]
    assert "such as a bag carried as a clutch" in kind["description"]


def test_the_planner_is_told_a_result_is_filmed_happening(
    httpserver: HTTPServer,
    openai_server: Callable[..., None],
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    # Round 2 #8 s4: the bottle beside a clean toilet. Julian: "there is no evidence that the
    # toilet is so clean right now ... we have to show."
    planning_through_openai(openai_server, product_page_url, PLAN)

    say(f"Make an ad for {product_page_url}")

    instructions = the_plan_request(httpserver)["instructions"]
    assert (
        "A result is filmed happening, from before to after in the same clip. A clean or "
        "finished thing with the product beside it, with no change filmed, never shows a "
        "result."
    ) in instructions


def test_the_planner_is_told_a_finishing_step_gets_no_broll_scene(
    httpserver: HTTPServer,
    openai_server: Callable[..., None],
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    # Round 2 fixes free check (10 Oct): #8 got a B-roll scene of the flush alone, with
    # stains still in the bowl and a different toilet from the scrub. Julian 02:59: add it.
    planning_through_openai(openai_server, product_page_url, PLAN)

    say(f"Make an ad for {product_page_url}")

    instructions = the_plan_request(httpserver)["instructions"]
    assert FINISHING_STEP in instructions


def test_the_planner_is_told_a_scene_needs_only_how_a_thing_looks(
    httpserver: HTTPServer,
    openai_server: Callable[..., None],
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    # Round 2 #12 s5: its need was "the edge-first drop", photo 2, the shop's drop photo; the
    # start picture copied its low hand, shoe and camera height. Julian 03:58: the picture
    # model copies the photo whatever its job says.
    planning_through_openai(openai_server, product_page_url, PLAN)

    say(f"Make an ad for {product_page_url}")

    request = the_plan_request(httpserver)
    instructions = request["instructions"]
    assert (
        "- Its needs: only how a thing looks that the main photo can't show, such as what a "
        "gel looks like out of the tube. Never an action, a pose or a movement, such as a "
        "drop or a hand holding it: the action is said in words only."
    ) in instructions
    needs = request["text"]["format"]["schema"]["$defs"]["PlannedScene"]["properties"]["needs"]
    assert "only how a thing looks" in needs["description"]


def test_the_planner_is_told_to_ask_for_a_before_and_after_nobody_could_picture(
    httpserver: HTTPServer,
    openai_server: Callable[..., None],
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    planning_through_openai(openai_server, product_page_url, PLAN)

    say(f"Make an ad for {product_page_url}")

    instructions = the_plan_request(httpserver)["instructions"]
    # N3's shower cleaner was planned with a result nobody could picture and
    # no photo of it, so the video guessed; a dirty toilet scrubbed clean needed no photo.
    assert "in only four cases" in instructions
    # N3's soap scum was judged common knowledge, so it never asked: what decides is whether
    # a camera can see it, not whether people know the word.
    assert "a thin film, haze, cloudiness or water spots" in instructions
    assert "even when everyone knows the word" in instructions
    assert "a coloured mark anyone sees at a glance" in instructions
    assert "of it before and after" in instructions
    # Only a photo shows it: a page that says "removes the film" still leaves its look to a
    # guess, which is how N3's result was invented.
    assert "The page naming the result isn't enough" in instructions
    # Test ads (08 Oct), N3: told no, the planner offered wiping clean tile "without an
    # invented before view", and before looked the same as after. Julian: "the shop owner
    # must provide it."
    assert "without one, the ad shows no before and after of it" in instructions
    # Round 2 fix 2: "over another B-roll" made N3's filler; with none that proves its own
    # line, the person says the claim to camera.
    assert (
        "For a result: if they go ahead without one, no scene shows that film, before or "
        "after, and the voice says the claim over another B-roll that proves its own line, or, "
        "when none does, the person says it to camera."
    ) in instructions
    assert "Never offer another way to show it." in instructions
    assert 'For a missing "how to use": if they go ahead without one, ask again' in instructions


def test_the_planner_is_told_a_line_naming_the_problem_shows_the_problem(
    httpserver: HTTPServer,
    openai_server: Callable[..., None],
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    # Test ads (08 Oct), N3: the "builds up" line was talking; Julian: "showing like how the
    # showers can get dirty over time. As a B-roll scene." Creatify: "Before state (3-5s):
    # Show the problem visually".
    planning_through_openai(openai_server, product_page_url, PLAN)

    say(f"Make an ad for {product_page_url}")

    request = the_plan_request(httpserver)
    assert (
        '"shows the problem" when the line names the problem the product fixes, as the page '
        "names it: the problem as it really is before the product is used, with the product "
        "in view. Only a problem anyone sees at a glance, or one a photo shows. Never the "
        "problem getting worse or building up over time: show it as it is, and the voice says "
        "the rest"
    ) in request["instructions"]
    scene = request["text"]["format"]["schema"]["$defs"]["PlannedScene"]["properties"]
    assert "shows the problem" in json.dumps(scene["broll_kind"])
    assert scene["thin_film"]["description"] == (
        "True when the problem this scene shows, or the result it ends on, is a thin film, "
        "haze, cloudiness or water spots on a surface."
    )


def test_a_scene_showing_the_problem_is_stored_with_its_kind(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    plan = broll_scene(
        line="Tea rings stain every mug you own, and they never scrub out.",
        shows="a tea-stained mug beside the Stoneware Mug",
        broll_kind="shows the problem",
    )
    planning(fake_model, product_page_url, plan)

    say(f"Make an ad for {product_page_url}")

    assert broll_labels()[1] == ("shows the problem", "no face", "", "", [])


def test_a_scene_about_a_thin_film_is_planned_with_a_photo_of_it(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    plan = broll_scene(
        broll_kind="shows the problem",
        thin_film=True,
        needs=[{"what": "the cloudy film", "photos": [2]}],
    )
    planning(fake_model, product_page_url, plan)

    say(f"Make an ad for {product_page_url}")

    assert broll_labels()[1][0] == "shows the problem"


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
    # The mug's page has no record of the product, so the planner is given no price: the copy
    # model skips the price in the words, and the declared data's price is taken out.
    assert "24.00" not in handoff["page_text"]
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


def test_the_planner_is_told_to_copy_the_products_name_word_for_word_from_a_line(
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
        "Give the product's name exactly as the person says it in one of those lines, copied "
        "word for word from it: not the page's full title, and nothing the person doesn't say, "
        "such as a part in brackets or a symbol like ® or ™."
    ) in request["instructions"]
    plan = request["text"]["format"]["schema"]["$defs"]["Plan"]
    assert plan["properties"]["product_name"]["description"] == (
        "The product's name copied word for word from a line, exactly as the person says it "
        "there: not the page's full title."
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


def a_plan_with_ways(*kinds: str) -> dict[str, Any]:
    """PLAN with a scene of each kind after its opening line: "talking" to camera,
    "broll" showing the mug, "second state" showing the second of two end states, or
    "talking second state", said to camera yet given as a second state."""
    scenes: list[dict[str, Any]] = [{"line": "Meet the Stoneware Mug."}]
    for kind in kinds:
        line = {"line": "Holds 350 ml of hot tea or cold brew."}
        if kind == "talking":
            scenes.append(line)
        elif kind == "talking second state":
            scenes.append({**line, "second_state": True})
        else:
            scenes.append(broll({**line, "shows": "the mug in a hand"}))
            scenes[-1]["second_state"] = kind == "second state"
    return a_plan_with(scenes=scenes)


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
                    broll({"line": "The mug, poured.", "shows": "tea poured into the mug"}),
                ]
            ),
            'No line says "Stoneware Mug": at least one must.',
            id="the name said in no line",
        ),
        pytest.param(
            broll_scene(broll_kind=None),
            'A B-roll scene needs its kind: "does a job", "shows the problem" or "showcase".',
            id="a B-roll scene with no kind",
        ),
        pytest.param(
            broll_scene(person_shown=None),
            'A B-roll scene needs who is in it: "no face" or "has face".',
            id="a B-roll scene with no person label",
        ),
        pytest.param(
            broll_scene(broll_kind="looks good"),
            "Input should be 'does a job', 'shows the problem' or 'showcase'",
            id="a kind that is none of them",
        ),
        pytest.param(
            broll_scene(broll_kind="shows the problem", result="A clean mug."),
            'A "shows the problem" scene has no result.',
            id="a problem shown with a result",
        ),
        pytest.param(
            # Test ads (08 Oct), N3: wiping a film nobody could see looked the same before and
            # after. Without a photo of it, no scene is about it.
            broll_scene(broll_kind="shows the problem", thin_film=True),
            "A scene about a thin film needs a photo that shows it among its needs. Without "
            "one, make no scene about it: the voice says it over another B-roll.",
            id="a thin film with no photo of it",
        ),
        pytest.param(
            broll_scene(
                broll_kind="does a job", usage="Wipe it.", result="No film.", thin_film=True
            ),
            "A scene about a thin film needs a photo that shows it among its needs.",
            id="a thin film wiped away with no photo of it",
        ),
        pytest.param(
            talking_scene(thin_film=True),
            "A scene where the person talks to camera has no B-roll kind",
            id="a talking scene about a thin film",
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
            "Scene 2 would send 6 pictures (the main photo, one for each of its 4 needs and "
            "the presenter's portrait): 5 at most.",
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
            a_plan_with_ways("talking", "second state"),
            "Scene 3 shows a second state, so scene 2 must be B-roll showing the first.",
            id="a second state after a talking scene",
        ),
        pytest.param(
            a_plan_with_ways("broll", "second state", "second state"),
            "Scene 4 shows a third state: one claim has two B-roll scenes at most.",
            id="a third state",
        ),
        pytest.param(
            a_plan_with(scenes=[{"line": "Meet the Stoneware Mug.", "second_state": True}]),
            "Scene 1 is said to camera: only a B-roll scene shows a second state.",
            id="the opening scene given as a second state",
        ),
        pytest.param(
            a_plan_with_ways("broll", "talking second state"),
            "Scene 3 is said to camera: only a B-roll scene shows a second state.",
            id="a talking scene given as a second state",
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
