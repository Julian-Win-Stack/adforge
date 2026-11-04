"""The planning checks, driven through the chat. The producer's model is faked at the gateway
to read the page, plan the ad, make the person and call run_planning_checks, and each test
checks what the tool handed back, what was stored and what the checks' models were given."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel, meanwhile, turn
from gateway.models import ModelCall
from jobs.library import (
    LookFrame,
    OwnerPhoto,
    PagePhoto,
    PictureLabel,
    Recoloured,
    add_to_library,
)
from jobs.models import Job
from jobs.work import keep_photo

from .conftest import (
    FACTS_OK,
    FINISHING_STEP,
    NO_CHOICES,
    PLAN,
    READABLE,
    a_plan_with,
    broll,
    broll_labels,
    facts_ok,
    handoffs,
    lines,
    paid_for,
    picture,
    plan_with,
    results_of,
)
from .test_producer_plan import current_plan, the_library_and_its_video_are_ready

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = [pytest.mark.django_db(transaction=True)]


def checking(
    fake_model: FakeModel,
    link: str,
    plan: dict[str, Any],
    *,
    target_seconds: int | None = None,
    reply: str = "Here's where your ad stands.",
) -> None:
    """Script the producer to read `link`, plan the ad with the planner answering `plan`,
    make the person and run the planning checks, then reply."""
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": link, "target_seconds": target_seconds})]),
        turn(calls=[("plan_ad", {})]),
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(says=reply),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", current_plan(plan))


def choosing_length(fake_model: FakeModel, choice: str) -> None:
    """Script the producer to run the checks again with the user's choice about the length."""
    fake_model.respond(
        "produce",
        turn(calls=[("run_planning_checks", {**NO_CHOICES, "length_choice": choice})]),
        turn(says="Here's where your ad stands."),
    )


def failing(scene: int, problem: str, *, passing: tuple[int, ...] = ()) -> dict[str, Any]:
    """What the fact check answers when `scene` gives the wrong price, and `passing` match."""
    return {
        "decision": "checked",
        "reason": f"The price in scene {scene} isn't the page's.",
        "question": None,
        "lines": [
            *facts_ok(*passing)["lines"],
            {
                "scene": scene,
                "verdict": "wrong",
                "wrong": "line",
                "problem": problem,
                "page_says": "$24.00",
            },
        ],
    }


# --- Lines that fail the fact check ---------------------------------------------------------


def test_a_line_that_fails_the_fact_check_is_rewritten_and_checked_again(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    scene_3, problem = (
        "Yours for $19.99, get the Stoneware Mug from our shop.",
        "The line says $19.99.",
    )
    checking(
        fake_model,
        product_page_url,
        plan_with(
            "Meet the Stoneware Mug from Kiln & Co for tea.",
            "Holds 350 ml for your favourite hot and cold drinks.",
            scene_3,
        ),
    )
    fake_model.respond("fact_check", failing(3, problem, passing=(1, 2)), facts_ok(3))
    fake_model.respond(
        "rewrite_line",
        {
            "line": "Yours for $24.00, get the Stoneware Mug from our shop.",
            "shows": "The presenter displays the mug from angle 3",
        },
    )

    say(f"Make an ad for {product_page_url}")

    assert results_of("run_planning_checks")[0].startswith("The checks passed.")
    assert lines() == [
        "Meet the Stoneware Mug from Kiln & Co for tea.",
        "Holds 350 ml for your favourite hot and cold drinks.",
        "Yours for $24.00, get the Stoneware Mug from our shop.",
    ]
    first, second = handoffs("fact_check")
    # The fact check is told the colour, so it can fail a line that says it.
    assert first["product_colour"] == "sage-green"
    assert [line["scene"] for line in first["lines"]] == [1, 2, 3]
    # Only the rewritten line is checked again.
    assert second["lines"] == [
        {
            "scene": 3,
            "line": "Yours for $24.00, get the Stoneware Mug from our shop.",
            "shows": "The presenter displays the mug from angle 3",
            "usage": None,
            "result": None,
        }
    ]
    (sent,) = handoffs("rewrite_line")
    assert (sent["scene"], sent["problems"]) == (
        3,
        [{"wrong": "line", "problem": problem, "page_says": "$24.00"}],
    )


def test_a_line_still_wrong_after_two_rewrites_is_handed_back_to_ask_the_user_about(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    checking(
        fake_model,
        product_page_url,
        plan_with(
            "Meet the mug for your favourite drinks at home today.",
            "Yours for $19.99, get the Stoneware Mug from our shop.",
            "Get your Stoneware Mug today for your favourite tea at home.",
        ),
    )
    fake_model.respond(
        "fact_check",
        failing(2, "The line says $19.99.", passing=(1, 3)),
        failing(2, "The line says $21.00."),
        failing(2, "The line says $22.00."),
    )
    fake_model.respond(
        "rewrite_line",
        {
            "line": "Yours for $21.00, get the Stoneware Mug from our shop.",
            "shows": "The presenter displays the mug from angle 3",
        },
        {
            "line": "Yours for $22.00, get the Stoneware Mug from our shop.",
            "shows": "The presenter displays the mug from angle 3",
        },
    )

    say(f"Make an ad for {product_page_url}")

    asked = results_of("run_planning_checks")[0]
    assert asked.startswith(
        "Scene 2's line still fails the fact check after 2 rewrites: \"Yours for $22.00"
    )
    assert "whether to keep this line" in asked
    assert "or give their own" in asked
    # The second rewrite was told both reasons the line failed, oldest first.
    first, second = handoffs("rewrite_line")
    assert [problem["problem"] for problem in second["problems"]] == [
        "The line says $19.99.",
        "The line says $21.00.",
    ]
    assert Job.objects.get().status != "ready_to_render"


# --- A page the fact check can't settle ------------------------------------------------------


def test_an_unclear_page_is_asked_about_straight_away_and_checked_again_with_the_answer(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    question = "Is the mug $24.00 or $28.00?"
    checking(fake_model, product_page_url, PLAN, reply=question)
    fake_model.respond(
        "fact_check",
        {
            "decision": "unclear",
            "reason": "The page gives two prices for the mug.",
            "question": question,
            "lines": [],
        },
    )
    say(f"Make an ad for {product_page_url}")

    (asked,) = results_of("run_planning_checks")
    assert asked.startswith(
        f"{question} Why: The page gives two prices for the mug. Ask the shop owner, then run "
        "the checks again.\n"
    )
    # Asked straight away: nothing was rewritten first.
    assert "rewrite_line" not in paid_for()

    fake_model.respond(
        "produce",
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(says="Every line checks out."),
    )
    fake_model.respond("fact_check", FACTS_OK)
    say("It's $24.00.")

    assert results_of("run_planning_checks")[1].startswith("The checks passed.")
    # The check is read the question next to its answer.
    assert handoffs("fact_check")[1]["conversation"][-2:] == [
        {"by": "producer", "text": question},
        {"by": "user", "text": "It's $24.00."},
    ]
    # Answering the check doesn't plan the ad again.
    assert paid_for().count("plan_ad") == 1


# --- The script's length ---------------------------------------------------------------------


# The 33-word plan runs 13.2 seconds at the fixed 2.5 words per second.
@pytest.mark.parametrize("target_seconds", [12, 14])
def test_a_script_no_more_than_2_seconds_over_its_target_fits(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], target_seconds: int
) -> None:
    checking(fake_model, product_page_url, PLAN, target_seconds=target_seconds)
    fake_model.respond("fact_check", FACTS_OK)

    say(f"Make a {target_seconds} second ad for {product_page_url}")

    (result,) = results_of("run_planning_checks")
    assert result.splitlines()[0] == (
        "The checks passed. Every line matches the product page, and the script fits your "
        f"{target_seconds}-second target. The ad is ready to render."
    )
    assert Job.objects.get().status == "ready_to_render"


def test_a_script_too_long_for_its_target_is_handed_back_to_ask_the_user_about(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    checking(fake_model, product_page_url, PLAN, target_seconds=6)
    fake_model.respond("fact_check", FACTS_OK)

    say(f"Make a 6 second ad for {product_page_url}")

    (result,) = results_of("run_planning_checks")
    assert result.splitlines()[0] == (
        "Your script runs about 13.2 seconds, 7.2 over your 6-second target. Why: At the "
        "fixed speaking speed the script runs 13.2 seconds, more than 2 seconds over the 6 "
        "seconds you asked for. Ask the shop owner whether to shorten it to fit, or keep it "
        "longer."
    )
    assert "shorten_script" not in paid_for()
    assert Job.objects.get().status != "ready_to_render"


@pytest.fixture
def asked_about_length(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    """A chat whose 13.2-second script ran over its 8-second target, and whose producer has
    asked the user whether to shorten it."""
    checking(
        fake_model,
        product_page_url,
        PLAN,
        target_seconds=8,
        reply="Your script runs 4 seconds over. Shorten it, or keep it longer?",
    )
    fake_model.respond("fact_check", FACTS_OK)
    say(f"Make an 8 second ad for {product_page_url}")
    assert "over your 8-second target" in results_of("run_planning_checks")[0]


def test_a_script_the_user_chooses_to_shorten_is_rewritten_and_its_new_lines_checked(
    fake_model: FakeModel, asked_about_length: None, say: Callable[..., None]
) -> None:
    choosing_length(fake_model, "shorten")
    # Keep the first line and shorten the remainder to fit the target.
    fake_model.respond(
        "shorten_script",
        {
            "lines": [
                {"scene": 1, "line": PLAN["plan"]["scenes"][0]["line"]},
                {"scene": 3, "line": "Yours for $24.00, today for your favourite drinks at home."},
            ]
        },
    )
    fake_model.respond("fact_check", facts_ok(2))

    say("Shorten it")

    assert results_of("run_planning_checks")[1].startswith("The checks passed.")
    assert lines() == [
        PLAN["plan"]["scenes"][0]["line"],
        "Yours for $24.00, today for your favourite drinks at home.",
    ]
    (sent,) = handoffs("shorten_script")
    # Eight seconds plus the two allowed over, at 2.5 words per second.
    assert (sent["target_seconds"], sent["most_words"]) == (8, 25)
    # The unchanged first line passed before, so only the new second line is checked.
    assert handoffs("fact_check")[1]["lines"] == [
        {
            "scene": 2,
            "line": "Yours for $24.00, today for your favourite drinks at home.",
            "shows": PLAN["plan"]["scenes"][2]["shows"],
            "usage": None,
            "result": None,
        }
    ]


def test_a_shortened_scene_keeps_its_part_of_the_script(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    parts = ["hook", "result", "call to action"]
    scenes = [
        {**scene, "part": part} for scene, part in zip(PLAN["plan"]["scenes"], parts, strict=True)
    ]
    checking(
        fake_model,
        product_page_url,
        a_plan_with(scenes=scenes),
        target_seconds=8,
        reply="Your script runs 4 seconds over. Shorten it, or keep it longer?",
    )
    fake_model.respond("fact_check", FACTS_OK)
    say(f"Make an 8 second ad for {product_page_url}")
    choosing_length(fake_model, "shorten")
    fake_model.respond(
        "shorten_script",
        {
            "lines": [
                {"scene": 1, "line": PLAN["plan"]["scenes"][0]["line"]},
                {"scene": 3, "line": "Yours for $24.00, today for your favourite drinks at home."},
            ]
        },
    )
    fake_model.respond("fact_check", facts_ok(2))

    say("Shorten it")

    assert list(Job.objects.get().scenes.values_list("number", "part")) == [
        (1, "hook"),
        (2, "call to action"),
    ]


@pytest.mark.parametrize(
    ("kept", "second_states"),
    [
        pytest.param((1, 3, 4, 5), [False, False, True, False], id="both ways kept"),
        pytest.param((1, 2, 4, 5), [False, False, False, False], id="the first way dropped"),
    ],
)
def test_a_shortened_second_state_stays_one_while_the_first_plays_just_before_it(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    kept: tuple[int, ...],
    second_states: list[bool],
) -> None:
    sip = {"line": "Sip hot tea from it on a cold and rainy morning.", "shows": "a hand lifts it"}
    chill = {"line": "Or fill it with ice and cold brew on a summer day.", "shows": "ice drops in"}
    scenes = [
        {"line": "Meet the Stoneware Mug from Kiln & Co for tea."},
        {"line": "It's hand-thrown in a small studio by one local potter."},
        broll(sip),
        broll({**chill, "second_state": True}),
        {"line": "Yours for $24.00, get the Stoneware Mug from our shop."},
    ]
    checking(
        fake_model,
        product_page_url,
        a_plan_with(scenes=scenes),
        target_seconds=8,
        reply="Your script runs over. Shorten it, or keep it longer?",
    )
    fake_model.respond("fact_check", facts_ok(1, 2, 3, 4, 5))
    say(f"Make an 8 second ad for {product_page_url}")
    choosing_length(fake_model, "shorten")
    fake_model.respond(
        "shorten_script",
        {"lines": [{"scene": number, "line": scenes[number - 1]["line"]} for number in kept]},
    )

    say("Shorten it")

    assert list(Job.objects.get().scenes.values_list("second_state", flat=True)) == (second_states)


def test_shortening_is_told_to_keep_the_hook_and_the_call_to_action(
    fake_model: FakeModel, asked_about_length: None, say: Callable[..., None]
) -> None:
    # Shortening could drop the hook or the call to action scene.
    told: list[str] = []

    def shorten(request: Any) -> dict[str, Any]:
        told.append(request.instructions)
        return {
            "lines": [
                {"scene": 1, "line": PLAN["plan"]["scenes"][0]["line"]},
                {"scene": 3, "line": "Yours for $24.00, today for your favourite drinks at home."},
            ]
        }

    choosing_length(fake_model, "shorten")
    fake_model.answer_unscripted("shorten_script", shorten)
    fake_model.respond("fact_check", facts_ok(2))

    say("Shorten it")

    (instructions,) = told
    assert "Keep the first scene, the hook, and the last, the call to action" in instructions


def test_a_script_still_too_long_after_two_shortenings_is_asked_about_again(
    fake_model: FakeModel, asked_about_length: None, say: Callable[..., None]
) -> None:
    choosing_length(fake_model, "shorten")
    # Both attempts still run more than two seconds over the target.
    fake_model.respond(
        "shorten_script",
        {
            "lines": [
                {"scene": 1, "line": "Meet the Stoneware Mug from Kiln & Co for tea."},
                {
                    "scene": 2,
                    "line": (
                        "Hand-thrown, holds 350 ml, and dishwasher safe for your favourite "
                        "tea and coffee at home every morning and evening."
                    ),
                },
            ]
        },
        {
            "lines": [
                {"scene": 1, "line": "Meet the Stoneware Mug from Kiln & Co for tea."},
                {
                    "scene": 2,
                    "line": (
                        "Hand-thrown, it holds 350 ml for your favourite tea and coffee, "
                        "yours for $24.00 from our shop today."
                    ),
                },
            ]
        },
    )
    fake_model.respond("fact_check", facts_ok(1, 2), facts_ok(2))

    say("Shorten it")

    assert results_of("run_planning_checks")[1].startswith(
        "Your script runs about 11.2 seconds, 3.2 over your 8-second target."
    )
    assert paid_for().count("shorten_script") == 2

    # Choosing to shorten again gives the producer two more tries.
    choosing_length(fake_model, "shorten")
    fake_model.respond(
        "shorten_script",
        {
            "lines": [
                {"scene": 1, "line": "Meet the Stoneware Mug for your favourite drinks at home."},
                {"scene": 2, "line": "Yours for $24.00, get the Stoneware Mug from our shop."},
            ]
        },
    )
    fake_model.respond("fact_check", facts_ok(1, 2))
    say("Shorten it again")

    assert results_of("run_planning_checks")[2].startswith("The checks passed.")
    assert lines() == [
        "Meet the Stoneware Mug for your favourite drinks at home.",
        "Yours for $24.00, get the Stoneware Mug from our shop.",
    ]


def test_a_script_the_user_chooses_to_keep_longer_goes_on_unchanged(
    fake_model: FakeModel, asked_about_length: None, say: Callable[..., None]
) -> None:
    choosing_length(fake_model, "keep_longer")

    say("Keep it longer")

    assert results_of("run_planning_checks")[1].splitlines()[0] == (
        "The checks passed. Every line matches the product page. The script runs longer than "
        "your 8-second target, as you chose. The ad is ready to render."
    )
    assert lines() == [scene["line"] for scene in PLAN["plan"]["scenes"]]
    assert "shorten_script" not in paid_for()


# --- Scenes that show something while the line is said ---------------------------------------

POUR = "tea poured from a teapot into the mug"
# Each kind uses the fixed 2.5-word speaking speed.
SAID_OVER = "Hand-thrown, holds 350 ml, and dishwasher safe at home too."


def showing(shows: str) -> dict[str, Any]:
    """The mug plan, with its second scene showing `shows` while its line is said."""
    scenes = [
        {"line": "Meet the Stoneware Mug from Kiln & Co for tea."},
        broll({"line": SAID_OVER, "shows": shows}),
        {"line": "Yours for $24.00, get the Stoneware Mug from our shop."},
    ]
    return {**PLAN, "plan": {**PLAN["plan"], "scenes": scenes}}


def shows_wrong(
    problem: str, *, passing: tuple[int, ...] = (), wrong: str | None = "shows"
) -> dict[str, Any]:
    """What the fact check answers when what scene 2 shows isn't supported: with `wrong`
    "both", its line isn't either."""
    return {
        "decision": "checked",
        "reason": "Nothing supports what scene 2 shows.",
        "question": None,
        "lines": [
            *facts_ok(*passing)["lines"],
            {
                "scene": 2,
                "verdict": "wrong",
                "wrong": wrong,
                "problem": problem,
                "page_says": "The page doesn't mention it.",
            },
        ],
    }


def images_shown(purpose: str) -> list[list[str]]:
    """The label of each picture shown to each call for `purpose`, oldest first."""
    return [
        [image["label"] for image in images]
        for images in ModelCall.objects.filter(purpose=purpose)
        .order_by("created_at", "id")
        .values_list("images", flat=True)
    ]


def rechecked() -> list[list[dict[str, Any]]]:
    """The lines handed to each fact check after the first, oldest first."""
    return [handoff["lines"] for handoff in handoffs("fact_check")[1:]]


@pytest.fixture
def rewrote_what_scene_2_shows(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    """A chat whose scene 2 showed the tea poured, which the page doesn't support, and was
    rewritten to show the mug turned in a hand, which passed."""
    checking(fake_model, product_page_url, showing(POUR))
    fake_model.respond(
        "fact_check", shows_wrong("The page doesn't mention tea.", passing=(1, 3)), facts_ok(2)
    )
    fake_model.respond(
        "rewrite_line",
        broll({"line": SAID_OVER, "shows": "the mug turned in a hand"}),
    )
    say(f"Make an ad for {product_page_url}")


def test_a_rewrite_is_told_a_broll_scene_films_one_action_as_the_planner_is(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    # A rewrite that isn't told could pack the steps back into one scene.
    told: list[str] = []

    def rewrite(request: Any) -> dict[str, Any]:
        told.append(request.instructions)
        return broll({"line": SAID_OVER, "shows": "the mug turned in a hand"})

    checking(fake_model, product_page_url, showing(POUR))
    fake_model.respond(
        "fact_check", shows_wrong("The page doesn't mention tea.", passing=(1, 3)), facts_ok(2)
    )
    fake_model.answer_unscripted("rewrite_line", rewrite)

    say(f"Make an ad for {product_page_url}")

    (instructions,) = told
    assert "A B-roll scene films one action" in instructions
    assert "film only the main step" in instructions
    assert "even for steps that flow into each other" in instructions
    assert "is a movement of its own: a scene films it or what comes after it" in instructions
    assert "with one verb for what moves" in instructions
    # The same fixes as the planner's (test ads, 08 Oct): N5's turn kept during its glide,
    # #12's phone never turned to a side its start picture doesn't show. Camera movement
    # may accompany the action under the approved Nov 1 rule change.
    assert "If the page says something happens while doing it, keep that part." in instructions
    assert "A hand never turns, flips or spins the product" in instructions
    assert "never a camera move" not in instructions
    assert "A camera movement may accompany that action; it does not replace it." in instructions
    # Round 2 fixes free check (10 Oct): #8's flush got a B-roll scene of its own.
    assert FINISHING_STEP in instructions
    # A rewrite changes one scene: it can't add a talking scene for the other steps.
    assert "name the other steps" not in instructions
    assert "Keep its place in the whole script and its kind." in instructions
    assert "Both kinds have an action: never give null shows." in instructions


def test_the_rewrite_is_handed_what_each_scene_shows_and_told_it_was_the_shows_that_failed(
    rewrote_what_scene_2_shows: None,
) -> None:
    (sent,) = handoffs("rewrite_line")
    # The whole script, with what each scene shows while its line is said.
    assert sent["script"] == [
        {
            "scene": 1,
            "line": "Meet the Stoneware Mug from Kiln & Co for tea.",
            "shows": "The presenter displays the mug from angle 1",
            "usage": None,
            "result": None,
        },
        {
            "scene": 2,
            "line": SAID_OVER,
            "shows": POUR,
            "usage": None,
            "result": None,
        },
        {
            "scene": 3,
            "line": "Yours for $24.00, get the Stoneware Mug from our shop.",
            "shows": "The presenter displays the mug from angle 3",
            "usage": None,
            "result": None,
        },
    ]
    # Which part of the scene failed: what it shows, not its line.
    assert sent["problems"] == [
        {
            "wrong": "shows",
            "problem": "The page doesn't mention tea.",
            "page_says": "The page doesn't mention it.",
        }
    ]


def test_talking_scene_actions_are_checked_with_the_product_photos(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    checking(fake_model, product_page_url, PLAN)
    fake_model.respond("fact_check", FACTS_OK)

    say(f"Make an ad for {product_page_url}")

    assert images_shown("fact_check") == [["Photo 1"]]


# Each rule is tested in tests/test_check_answers.py; one case here goes through the chat.
@pytest.mark.parametrize(
    ("plan", "verdict", "told"),
    [
        pytest.param(
            PLAN,
            shows_wrong("The page doesn't mention tea.", passing=(1, 3), wrong=None),
            'A "wrong" line needs what is wrong',
            id="a talking scene's shows found wrong",
        ),
    ],
)
def test_a_fact_check_that_doesnt_say_which_part_of_the_scene_is_wrong_cant_be_used(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    plan: dict[str, Any],
    verdict: dict[str, Any],
    told: str,
) -> None:
    checking(fake_model, product_page_url, plan)
    # Told why and asked again, the check breaks the same rule again.
    fake_model.respond("fact_check", verdict, verdict)

    say(f"Make an ad for {product_page_url}")

    (failed,) = results_of("run_planning_checks")
    assert failed.startswith("Failed: a model's answer couldn't be used")
    assert told in failed
    assert Job.objects.get().scenes.filter(fact_checked=True).count() == 0


def test_a_rewrite_never_changes_a_talking_scenes_kind(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    checking(fake_model, product_page_url, PLAN)
    fake_model.respond("fact_check", failing(3, "The line says $19.99.", passing=(1, 2)))
    shows_a_price_tag = broll(
        {
            "kind": "broll",
            "line": "Yours for $24.00, get the Stoneware Mug from our shop.",
            "shows": "a price tag",
        }
    )
    # Told why and asked again, the rewrite breaks the same rule again.
    fake_model.respond("rewrite_line", shows_a_price_tag, shows_a_price_tag)

    say(f"Make an ad for {product_page_url}")

    (failed,) = results_of("run_planning_checks")
    assert "Keep the scene's kind" in failed
    assert Job.objects.get().scenes.get(number=3).shows == PLAN["plan"]["scenes"][2]["shows"]


def test_a_rewrite_cannot_remove_a_talking_scenes_action(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    checking(
        fake_model,
        product_page_url,
        plan_with(
            "Meet the Stoneware Mug from Kiln & Co for tea.",
            "Holds 350 ml for your favourite hot and cold drinks.",
            "Yours for $19.99, get the Stoneware Mug from our shop.",
        ),
    )
    fake_model.respond("fact_check", failing(3, "The line says $19.99.", passing=(1, 2)))
    reply = {"line": "Yours for $24.00, get the Stoneware Mug from our shop.", "shows": ""}
    fake_model.respond("rewrite_line", reply, reply)

    say(f"Make an ad for {product_page_url}")

    scene = Job.objects.get().scenes.get(number=3)
    assert scene.line == "Yours for $19.99, get the Stoneware Mug from our shop."
    assert scene.shows == "The presenter displays the mug from angle 3"
    assert results_of("run_planning_checks")[0].startswith("Failed:")


@pytest.fixture
def asked_about_what_scene_2_shows(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    """A chat whose scene 2 shows something still unsupported after 2 rewrites, and whose
    producer has asked the user what to do about it."""
    checking(
        fake_model,
        product_page_url,
        showing(POUR),
        reply="Keep scene 2, give your own line, or say it to camera?",
    )
    fake_model.respond(
        "fact_check",
        shows_wrong("The page doesn't mention tea.", passing=(1, 3)),
        shows_wrong("The page doesn't mention coffee."),
        shows_wrong("The page doesn't mention cocoa."),
    )
    fake_model.respond(
        "rewrite_line",
        broll({"line": SAID_OVER, "shows": "coffee poured into the mug"}),
        broll({"line": SAID_OVER, "shows": "cocoa poured into the mug"}),
    )
    say(f"Make an ad for {product_page_url}")


def test_a_scene_still_showing_the_unsupported_after_two_rewrites_is_asked_about(
    asked_about_what_scene_2_shows: None,
) -> None:
    (asked,) = results_of("run_planning_checks")
    assert asked.splitlines()[0] == (
        f'Scene 2\'s line still fails the fact check after 2 rewrites: "{SAID_OVER}" '
        "While it's said, the ad shows: cocoa poured into the mug. The page doesn't "
        "mention cocoa. The page says: The page doesn't mention it. Why: The line was "
        "rewritten 2 times and still failed the fact check, so you decide: the check itself "
        "may be wrong. Ask the shop owner whether to keep this line or give their own."
    )
    # The shop owner is never told the kinds of scene apart.
    assert "b-roll" not in asked.lower()


# --- A B-roll scene's details ----------------------------------------------------------------

USAGE = "tea poured in from a teapot"
RESULT = "the mug full of steaming tea"

# Scene 2 of `doing_a_job()`: the mug doing a job, which needs photo 2 for its glaze.
DOES_A_JOB: dict[str, Any] = {
    "line": SAID_OVER,
    "shows": POUR,
    "broll_kind": "does a job",
    "person_shown": "no face",
    "usage": USAGE,
    "result": RESULT,
    "needs": [{"what": "the glaze up close", "photos": [2]}],
}

# What the rewrite gives back for scene 2: every B-roll detail is new.
REWRITTEN: dict[str, Any] = {
    "line": "Hand-thrown, holds 350 ml, and dishwasher safe for daily drinks.",
    "shows": "the mug lifted out of a dishwasher rack",
    "broll_kind": "showcase",
    "person_shown": "no face",
    "usage": "taken out of the dishwasher",
    "result": None,
    "needs": [],
}


def doing_a_job(**scene_2: Any) -> dict[str, Any]:
    """The mug plan, with its second scene showing the mug doing a job."""
    plan = showing(POUR)
    scenes = list(plan["plan"]["scenes"])
    scenes[1] = {**DOES_A_JOB, **scene_2}
    return {**plan, "plan": {**plan["plan"], "scenes": scenes}}


@pytest.fixture
def rewrote_scene_2_whole(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    """A chat whose scene 2's usage the page doesn't state, rewritten whole, which passed."""
    # Scene 2 picks library item 2, the page's photo 2, for its glaze (spec #210 point 11).
    checking(
        fake_model,
        product_page_url,
        doing_a_job(needs=[], scene_pictures=[{"number": 2, "for": "content"}]),
    )
    fake_model.respond(
        "fact_check",
        shows_wrong("The page doesn't say tea is poured in.", passing=(1, 3)),
        facts_ok(2),
    )
    fake_model.respond("rewrite_line", REWRITTEN)
    say(f"Make an ad for {product_page_url}")


def test_a_broll_scenes_details_and_needed_photos_are_handed_to_the_fact_check_and_the_rewrite(
    rewrote_scene_2_whole: None,
) -> None:
    # The fact check is handed the scene's usage and result with what it shows.
    assert handoffs("fact_check")[0]["lines"][1] == {
        "scene": 2,
        "line": SAID_OVER,
        "shows": POUR,
        "usage": USAGE,
        "result": RESULT,
    }
    # Each fact check is shown photo 1, in the ad's sage green (photo 2 shows the mug in
    # cream), and each B-roll scene's pictures: scene 2 first picked photo 2, and its rewrite
    # picks none.
    assert images_shown("fact_check") == [["Photo 1", "Photo 2"], ["Photo 1"]]
    # The rewrite is shown the library's sheets, as the planner is, and told how many
    # pictures it holds and which show the colour, so it can give back what its scene needs.
    assert images_shown("rewrite_line") == [
        ["Library pictures 1 to 2 (each numbered and labelled under it)"]
    ]
    (sent,) = handoffs("rewrite_line")
    assert (sent["library_pictures"], sent["pictures_not_shown"], sent["colour_photos"]) == (
        2,
        0,
        [1],
    )


def test_a_rewrite_on_a_library_bigger_than_the_sheets_hold_is_shown_the_same_200(
    fake_model: FakeModel, page_read: str, say: Callable[..., None]
) -> None:
    # The page's 2 photos and 208 more pictures: 210 in the library.
    job = Job.objects.get()
    front = job.photos.get(position=1).file
    for _ in range(208):
        add_to_library(
            job,
            front,
            PictureLabel(kind="side", side="front", alone="yes", colour="green", label="The mug."),
            PagePhoto(photo=1),
        )
    fake_model.respond(
        "produce",
        turn(calls=[("plan_ad", {})]),
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(says="Here's where your ad stands."),
    )
    fake_model.respond("plan_ad", current_plan(doing_a_job()))
    fake_model.respond(
        "fact_check",
        shows_wrong("The page doesn't say tea is poured in.", passing=(1, 3)),
        facts_ok(2),
    )
    fake_model.respond("rewrite_line", REWRITTEN)

    say("Plan it and check it")

    (shown,) = images_shown("rewrite_line")
    assert (len(shown), shown[-1]) == (
        50,
        "Library pictures 197 to 200 (each numbered and labelled under it)",
    )
    assert handoffs("rewrite_line")[0]["pictures_not_shown"] == 10
    # The plan left the same pictures out, and the chat was told once.
    job.refresh_from_db()
    assert [warning["text"] for warning in job.warnings].count(
        "The product's library has 210 pictures, more than the 200 the planner can be shown, "
        "so 10 of them are not shown: pictures 201 to 210 are left out when the ad is planned "
        "or a scene is rewritten."
    ) == 1


def test_a_rewrite_names_pictures_by_library_number_and_its_needs_are_kept_as_photos(
    fake_model: FakeModel, page_read: str, say: Callable[..., None]
) -> None:
    # The page's 2 photos are pictures 1 and 2; picture 3 is a look frame; picture 4 is the
    # shop owner's photo, the job's photo 3: its library number isn't its photo's number.
    job = Job.objects.get()
    mug = PictureLabel(kind="side", side="front", alone="yes", colour="green", label="The mug.")
    add_to_library(job, job.photos.get(position=1).file, mug, LookFrame(video=1, second=1.0))
    owners = keep_photo(job, 3, picture(90, 90, (143, 170, 140)), "image/png", has_face=False)
    add_to_library(job, owners.file, mug, OwnerPhoto(original=owners.file))
    # Picture 5 is the owner's photo recoloured: never in the ad's colour as its photo is.
    red = PictureLabel(kind="side", side="front", alone="yes", colour="red", label="The mug.")
    add_to_library(job, owners.file, red, Recoloured(of=4, colour="red"))
    fake_model.respond(
        "produce",
        turn(calls=[("plan_ad", {})]),
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(says="Here's where your ad stands."),
    )
    planned = doing_a_job()
    fake_model.respond(
        "plan_ad", current_plan({**planned, "plan": {**planned["plan"], "colour_photos": [4]}})
    )
    problem = "The page doesn't say tea is poured in."
    fake_model.respond(
        "fact_check", shows_wrong(problem, passing=(1, 3)), shows_wrong(problem), facts_ok(2)
    )
    # Each rewrite needs the look frame, which has no product photo, and the owner's photo.
    rewritten = {**REWRITTEN, "needs": [{"what": "the mug's base", "photos": [3, 4]}]}
    fake_model.respond("rewrite_line", rewritten, rewritten)

    say("Plan it and check it")

    # The rewrite is told the colour photos by library number, and its needs are kept as the
    # product photos those numbers are.
    assert handoffs("rewrite_line")[0]["colour_photos"] == [4]
    assert Job.objects.get().scenes.get(number=2).needs == [
        {"what": "the mug's base", "photos": [3]}
    ]
    # Rewritten twice the same way, the chat is told once of the picture left out.
    told = (
        "Library picture 3 has no product photo a starting picture can be made from, so it is "
        "left out of the photos the scenes need."
    )
    assert [warning["text"] for warning in Job.objects.get().warnings].count(told) == 1


def test_a_usage_or_result_the_page_doesnt_state_fails_the_scenes_fact_check(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    # The check fails the scene for its usage here; a result it doesn't state takes the same
    # path, as the code only passes the check's problem on.
    problem = "The page doesn't say tea is poured in."
    checking(fake_model, product_page_url, doing_a_job())
    fake_model.respond(
        "fact_check", shows_wrong(problem, passing=(1, 3)), shows_wrong(problem), facts_ok(2)
    )
    fake_model.respond("rewrite_line", REWRITTEN, REWRITTEN)

    say(f"Make an ad for {product_page_url}")

    assert [sent["problems"][-1]["problem"] for sent in handoffs("rewrite_line")] == [
        problem,
        problem,
    ]
    assert Job.objects.get().scenes.get(number=2).fact_checked


def test_a_rewrite_gives_back_every_broll_detail_which_is_stored_and_fact_checked_again(
    rewrote_scene_2_whole: None,
) -> None:
    scene = Job.objects.get().scenes.get(number=2)
    assert (scene.line, scene.shows) == (REWRITTEN["line"], REWRITTEN["shows"])
    assert broll_labels()[1] == ("showcase", "no face", "taken out of the dishwasher", "", [])
    # Only the rewritten scene is checked again, with its new details.
    assert rechecked() == [
        [
            {
                "scene": 2,
                "line": REWRITTEN["line"],
                "shows": REWRITTEN["shows"],
                "usage": "taken out of the dishwasher",
                "result": None,
            }
        ]
    ]


def test_a_talking_scene_rewrite_is_shown_the_library(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    checking(fake_model, product_page_url, a_plan_with())
    fake_model.respond(
        "fact_check", failing(3, "The line says $19.99.", passing=(1, 2)), facts_ok(3)
    )
    fake_model.respond(
        "rewrite_line",
        {
            "line": "Yours for $24.00, get the Stoneware Mug from our shop.",
            "shows": "The presenter displays the mug from angle 3",
        },
    )

    say(f"Make an ad for {product_page_url}")

    assert images_shown("rewrite_line") == [
        ["Library pictures 1 to 2 (each numbered and labelled under it)"]
    ]


# The other B-roll rules are tested in tests/test_check_answers.py. This case goes through the
# chat, as only it shows the rewrite is read against the job's own photos.
@pytest.mark.parametrize(
    ("rewrite", "told"),
    [
        pytest.param(
            {**DOES_A_JOB, "needs": [{"what": "the glaze up close", "photos": [3]}]},
            "There's no picture 3 in the library.",
            id="a picture the library doesn't have",
        ),
    ],
)
def test_a_rewrite_that_breaks_the_broll_rules_cant_be_used(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    rewrite: dict[str, Any],
    told: str,
) -> None:
    checking(fake_model, product_page_url, doing_a_job())
    fake_model.respond(
        "fact_check", shows_wrong("The page doesn't say tea is poured in.", passing=(1, 3))
    )
    # Told why and asked again, the rewrite breaks the same rule again.
    fake_model.respond("rewrite_line", rewrite, rewrite)

    say(f"Make an ad for {product_page_url}")

    (failed,) = results_of("run_planning_checks")
    assert failed.startswith("Failed: a model's answer couldn't be used")
    assert told in failed
    assert broll_labels()[1] == (
        "does a job",
        "no face",
        USAGE,
        RESULT,
        [{"what": "the glaze up close", "photos": [2]}],
    )


@pytest.mark.parametrize("shows", [None, "  "], ids=["null", "blank"])
def test_a_rewrite_cannot_remove_a_broll_scenes_action(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], shows: str | None
) -> None:
    # Scene 2 picks library item 2, the page's photo 2, for its glaze (spec #210 point 11).
    checking(
        fake_model,
        product_page_url,
        doing_a_job(needs=[], scene_pictures=[{"number": 2, "for": "content"}]),
    )
    fake_model.respond(
        "fact_check",
        shows_wrong("The page doesn't say tea is poured in.", passing=(1, 3)),
    )
    # The rewrite gives back every B-roll detail with the blank "shows".
    reply = {**DOES_A_JOB, "shows": shows}
    fake_model.respond("rewrite_line", reply, reply)

    say(f"Make an ad for {product_page_url}")

    scene = Job.objects.get().scenes.get(number=2)
    assert (scene.line, scene.shows, scene.fact_checked) == (SAID_OVER, POUR, False)
    assert scene.kind == "broll"
    assert results_of("run_planning_checks")[0].startswith("Failed:")


def checking_from_the_library(fake_model: FakeModel, link: str, plan: dict[str, Any]) -> None:
    """`checking`, the job's library and watched video made while the producer plans."""
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": link, "target_seconds": None})]),
        meanwhile(the_library_and_its_video_are_ready, turn(calls=[("plan_ad", {})])),
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(says="Here's where your ad stands."),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", current_plan(plan))


# Scene 2's action as the planner sent it: a picture of the mug in use and the video's pour.
PICKED: dict[str, Any] = {
    "scene_pictures": [{"number": 4, "for": "in-use"}],
    "piece": {"video": 1, "moment": 1},
}


@pytest.mark.parametrize(
    "rewrite",
    [
        pytest.param(REWRITTEN, id="another action"),
        pytest.param(
            {**DOES_A_JOB, "shows": "The presenter displays the mug from angle 3"},
            id="said to camera",
        ),
    ],
)
def test_a_scene_rewritten_to_another_action_or_to_camera_loses_its_scene_pictures_and_piece(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], rewrite: dict[str, Any]
) -> None:
    # The planner's picks were for the old action: kept, they would send its moment.
    checking_from_the_library(fake_model, product_page_url, doing_a_job(**PICKED))
    fake_model.respond(
        "fact_check",
        shows_wrong("The page doesn't say tea is poured in.", passing=(1, 3)),
        facts_ok(2),
    )
    fake_model.respond("rewrite_line", rewrite)

    say(f"Make an ad for {product_page_url}")

    scene = Job.objects.get().scenes.get(number=2)
    assert (scene.shows, scene.scene_pictures, scene.piece) == (rewrite["shows"] or "", [], None)


def test_a_scene_whose_line_alone_is_rewritten_keeps_its_scene_pictures_and_piece(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    checking_from_the_library(fake_model, product_page_url, doing_a_job(**PICKED))
    fake_model.respond(
        "fact_check",
        failing(2, "The line says something the page doesn't.", passing=(1, 3)),
        facts_ok(2),
    )
    fake_model.respond(
        "rewrite_line",
        {
            **DOES_A_JOB,
            "line": "Hand-thrown and dishwasher safe for your favourite drinks at home.",
        },
    )

    say(f"Make an ad for {product_page_url}")

    scene = Job.objects.get().scenes.get(number=2)
    assert (scene.shows, scene.scene_pictures, scene.piece) == (
        POUR,
        PICKED["scene_pictures"],
        PICKED["piece"],
    )


# --- A line too long for one scene -----------------------------------------------------------


# Forty words exceed the 35-word maximum.
TOO_LONG = " ".join(["Hand-thrown and dishwasher safe, it holds 350 ml."] * 5)
# Thirty-five words: exactly 14 seconds at the fixed speaking speed.
LONGEST = " ".join(["tea"] * 35)


# Ten words meet the four-second minimum for either kind.
SHORTENED = "Hand-thrown and dishwasher safe, it holds 350 ml at home."


def scene_2_saying(line: str, *, shows: str | None = None) -> dict[str, Any]:
    """The mug plan with scene 2 saying `line`, over what it `shows` if anything."""
    plan = plan_with(
        "Meet the Stoneware Mug from Kiln & Co for tea.",
        line,
        "Yours for $24.00, get the Stoneware Mug from our shop.",
    )
    plan["plan"]["scenes"][1] = broll({**plan["plan"]["scenes"][1], "shows": shows})
    return plan


# Both scene kinds allow 35 words, or 14 seconds.
@pytest.mark.parametrize(
    ("shows", "most_words"),
    [pytest.param(None, 35, id="talking"), pytest.param(POUR, 35, id="B-roll")],
)
def test_a_line_too_long_for_one_scene_is_shortened_to_the_most_words_and_checked_again(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    shows: str | None,
    most_words: int,
) -> None:
    # Scene 2 exceeds the same 35-word limit for either kind.
    checking(fake_model, product_page_url, scene_2_saying(TOO_LONG, shows=shows))
    fake_model.respond("fact_check", FACTS_OK, facts_ok(2))
    fake_model.respond("shorten_line", {"line": SHORTENED})

    say(f"Make an ad for {product_page_url}")

    assert lines() == [
        "Meet the Stoneware Mug from Kiln & Co for tea.",
        SHORTENED,
        "Yours for $24.00, get the Stoneware Mug from our shop.",
    ]
    # The shortener is told the most words the line may have, and handed the whole script
    # with what each scene shows.
    (sent,) = handoffs("shorten_line")
    assert (sent["scene"], sent["most_words"]) == (2, most_words)
    assert sent["script"] == [
        {
            "scene": 1,
            "line": "Meet the Stoneware Mug from Kiln & Co for tea.",
            "shows": "The presenter displays the mug from angle 1",
            "usage": None,
            "result": None,
        },
        {
            "scene": 2,
            "line": TOO_LONG,
            "shows": shows or "The presenter displays the mug from angle 2",
            "usage": None,
            "result": None,
        },
        {
            "scene": 3,
            "line": "Yours for $24.00, get the Stoneware Mug from our shop.",
            "shows": "The presenter displays the mug from angle 3",
            "usage": None,
            "result": None,
        },
    ]
    # The shortened line is fact checked again, with what its scene shows.
    assert rechecked() == [
        [
            {
                "scene": 2,
                "line": SHORTENED,
                "shows": shows or "The presenter displays the mug from angle 2",
                "usage": None,
                "result": None,
            }
        ]
    ]


@pytest.fixture
def asked_for_a_shorter_line(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    """Scene 2 is still too long after three shortenings; the producer asks for a line."""
    checking(
        fake_model,
        product_page_url,
        scene_2_saying(TOO_LONG),
        reply="Scene 2 is too long. What should it say?",
    )
    fake_model.respond("fact_check", FACTS_OK, facts_ok(2), facts_ok(2), facts_ok(2))
    fake_model.respond(
        "shorten_line",
        {"line": TOO_LONG},
        {"line": TOO_LONG + " Really."},
        {"line": TOO_LONG + " Really. Truly."},
    )
    say(f"Make an ad for {product_page_url}")


def test_a_line_still_too_long_after_three_shortenings_is_asked_about(
    asked_for_a_shorter_line: None,
) -> None:
    (asked,) = results_of("run_planning_checks")
    assert asked.splitlines()[0] == (
        "Scene 2's line still takes about 16.8 seconds to say after 3 shortenings, and a scene "
        f'can last at most 14 seconds: "{TOO_LONG} Really. Truly." Why: The line was '
        "shortened 3 times and is still too long for one scene, so you choose a shorter line. "
        "Ask the shop owner for a shorter line of their own."
    )
    assert paid_for().count("shorten_line") == 3


@pytest.mark.parametrize(
    "line_choice",
    [
        pytest.param({"scene": 2, "choice": "keep", "own_line": None}, id="kept"),
        pytest.param({"scene": 2, "choice": "own", "own_line": TOO_LONG}, id="the user's own"),
    ],
)
def test_a_line_too_long_for_one_scene_is_refused_as_the_users_choice(
    fake_model: FakeModel,
    asked_for_a_shorter_line: None,
    say: Callable[..., None],
    line_choice: dict[str, Any],
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("run_planning_checks", {**NO_CHOICES, "line_choices": [line_choice]})]),
        turn(says="That line is too long for one scene."),
    )

    say(f"Hmm. {TOO_LONG}")

    assert results_of("run_planning_checks")[1] == (
        "Refused: the line chosen for scene 2's line takes longer to say than a scene can last "
        "(14 seconds at the speaking speed), so it can't be used. Ask the shop owner for a "
        "shorter line. Nothing was done."
    )


@pytest.mark.parametrize(
    "line",
    [
        pytest.param("Holds 350 ml for your favourite hot and cold drinks.", id="short"),
        pytest.param(LONGEST, id="said in exactly 14 seconds"),
    ],
)
def test_a_line_the_user_gives_that_fits_in_one_scene_is_used_as_written(
    fake_model: FakeModel, asked_for_a_shorter_line: None, say: Callable[..., None], line: str
) -> None:
    own = {"scene": 2, "choice": "own", "own_line": line}
    fake_model.respond(
        "produce",
        turn(calls=[("run_planning_checks", {**NO_CHOICES, "line_choices": [own]})]),
        turn(says="I've used your line."),
    )

    say(f"Use this: {line}")

    assert (lines()[1], Job.objects.get().status) == (line, "ready_to_render")


def test_music_waits_for_a_line_too_long_for_one_scene(
    fake_model: FakeModel, asked_for_a_shorter_line: None, say: Callable[..., None]
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("create_music", {"mood": "light upbeat lo-fi"})]),
        turn(says="The music waits for a shorter line."),
    )

    say("Make the music meanwhile")

    assert results_of("create_music") == [
        "Refused: scene 2's line takes longer to say than a scene can last. Run the planning "
        "checks first. Nothing was done."
    ]


# --- Shortening a script with scenes that show something -------------------------------------


@pytest.fixture
def asked_about_length_with_a_pour(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    """An over-target script with a pour action and per-scene historical overlays."""
    plan = showing(POUR)
    first, pour, price = plan["plan"]["scenes"]
    plan["plan"]["scenes"] = [
        first,
        {**pour, "overlay": "Hand-thrown"},
        {**price, "overlay": "$24.00"},
    ]
    checking(fake_model, product_page_url, plan, target_seconds=8)
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    say(f"Make an 8 second ad for {product_page_url}")
    assert "over your 8-second target" in results_of("run_planning_checks")[0]


def shortening_to(fake_model: FakeModel, *lines: tuple[int, str]) -> None:
    """Script the producer to shorten the script when the user chooses to, and the shortening
    model to give back `lines`, each as the number of the scene it comes from and its line."""
    choosing_length(fake_model, "shorten")
    fake_model.respond(
        "shorten_script", {"lines": [{"scene": scene, "line": line} for scene, line in lines]}
    )


def scenes_now() -> list[tuple[str, str, str]]:
    """Each scene's line, what it shows and its overlay, in order."""
    return list(Job.objects.get().scenes.values_list("line", "shows", "overlay"))


def lines_and_shows() -> list[tuple[str, str]]:
    """Each scene's line and what it shows, in order."""
    return list(Job.objects.get().scenes.values_list("line", "shows"))


def test_shortening_keeps_what_each_scene_shows_and_checks_each_changed_line_with_it(
    fake_model: FakeModel, asked_about_length_with_a_pour: None, say: Callable[..., None]
) -> None:
    # Every scene stays, with new lines for the first two.
    shortening_to(
        fake_model,
        (1, "Meet the Stoneware Mug for your favourite drinks at home."),
        (2, "Holds 350 ml for your favourite hot and cold drinks."),
        (3, "Yours for $24.00, get the Stoneware Mug from our shop."),
    )
    fake_model.respond("fact_check", facts_ok(1, 2))
    fake_model.respond(
        "shorten_script",
        {
            "lines": [
                {"scene": 1, "line": "Meet the Stoneware Mug for your favourite drinks at home."},
                {"scene": 2, "line": "Holds 350 ml for your favourite hot and cold drinks."},
                {"scene": 3, "line": "Yours for $24.00, get the Stoneware Mug from our shop."},
            ]
        },
    )

    say("Shorten it")

    # The shortening model is handed what each scene shows.
    assert [handoffs("shorten_script")[0]["script"]] == [
        [
            {
                "scene": 1,
                "line": "Meet the Stoneware Mug from Kiln & Co for tea.",
                "shows": "The presenter displays the mug from angle 1",
                "usage": None,
                "result": None,
            },
            {
                "scene": 2,
                "line": SAID_OVER,
                "shows": POUR,
                "usage": None,
                "result": None,
            },
            {
                "scene": 3,
                "line": "Yours for $24.00, get the Stoneware Mug from our shop.",
                "shows": "The presenter displays the mug from angle 3",
                "usage": None,
                "result": None,
            },
        ]
    ]
    # A shortened line keeps what its scene shows.
    assert lines_and_shows() == [
        (
            "Meet the Stoneware Mug for your favourite drinks at home.",
            "The presenter displays the mug from angle 1",
        ),
        ("Holds 350 ml for your favourite hot and cold drinks.", POUR),
        (
            "Yours for $24.00, get the Stoneware Mug from our shop.",
            "The presenter displays the mug from angle 3",
        ),
    ]
    # Each line that changed is fact checked with what its scene shows.
    assert rechecked() == [
        [
            {
                "scene": 1,
                "line": "Meet the Stoneware Mug for your favourite drinks at home.",
                "shows": "The presenter displays the mug from angle 1",
                "usage": None,
                "result": None,
            },
            {
                "scene": 2,
                "line": "Holds 350 ml for your favourite hot and cold drinks.",
                "shows": POUR,
                "usage": None,
                "result": None,
            },
        ]
    ]


# A scene where the mug does a job, with the presenter's face and a need of its own.
POUR_DOES_A_JOB: dict[str, Any] = {
    "line": "Pour in hot tea and it stays warm in your hands.",
    "shows": POUR,
    "broll_kind": "does a job",
    "person_shown": "no face",
    "usage": "Pour a hot drink into the mug.",
    "result": "The mug full of steaming tea.",
    "needs": [{"what": "the handle", "photos": [2]}],
}


def test_a_line_moved_up_in_shortening_takes_its_broll_labels_with_it(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    # Scene 2, said to camera, is dropped, so the pour's line plays second.
    plan = a_plan_with(
        scenes=[
            {"line": "Meet the Stoneware Mug from Kiln & Co for tea."},
            {"line": "Hand-thrown, holds 350 ml, and dishwasher safe for daily drinks."},
            POUR_DOES_A_JOB,
        ]
    )
    checking(fake_model, product_page_url, plan, target_seconds=8)
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    say(f"Make an 8 second ad for {product_page_url}")
    shortening_to(
        fake_model,
        (1, "Meet the Stoneware Mug for your favourite drinks at home."),
        (3, "Pour in hot tea and it stays warm in your hands."),
    )
    fake_model.respond("fact_check", facts_ok(1, 2))

    say("Shorten it")

    assert lines_and_shows() == [
        (
            "Meet the Stoneware Mug for your favourite drinks at home.",
            "The presenter displays the mug from angle 1",
        ),
        ("Pour in hot tea and it stays warm in your hands.", POUR),
    ]
    assert broll_labels() == [
        ("", "", "", "", []),
        (
            "does a job",
            "no face",
            "Pour a hot drink into the mug.",
            "The mug full of steaming tea.",
            [{"what": "the handle", "photos": [2]}],
        ),
    ]


def test_a_scene_dropped_in_shortening_takes_its_overlay_and_a_moved_line_isnt_checked_again(
    fake_model: FakeModel, asked_about_length_with_a_pour: None, say: Callable[..., None]
) -> None:
    # Drop the pour; its action and overlay go with it.
    shortening_to(
        fake_model,
        (1, "Meet the Stoneware Mug from Kiln & Co for tea."),
        (3, "Yours for $24.00, get the Stoneware Mug from our shop."),
    )

    say("Shorten it")

    # "Hand-thrown" went with the pour, and the price kept its own overlay.
    assert list(Job.objects.get().scenes.values_list("overlay", flat=True)) == ["", "$24.00"]
    # The price's line moved up, but had already passed the fact check: no fact check after
    # the shortening.
    assert paid_for() == [
        "check_page",
        "copy_page_text",
        "note_face",
        "note_face",
        "label_photo",
        "label_photo",
        "plan_ad",
        "draw_person",
        "fact_check",
        "shorten_script",
    ]


def test_a_checked_line_put_over_what_another_scene_shows_is_fact_checked_again(
    fake_model: FakeModel, asked_about_length_with_a_pour: None, say: Callable[..., None]
) -> None:
    # Moving the checked price line to the pour requires a new check of that action.
    shortening_to(
        fake_model,
        (1, "Meet the Stoneware Mug from Kiln & Co for tea."),
        (2, "Yours for $24.00, get the Stoneware Mug from our shop."),
    )
    fake_model.respond("fact_check", facts_ok(2))

    say("Shorten it")

    assert rechecked() == [
        [
            {
                "scene": 2,
                "line": "Yours for $24.00, get the Stoneware Mug from our shop.",
                "shows": POUR,
                "usage": None,
                "result": None,
            }
        ]
    ]


# Each rule is tested in tests/test_check_answers.py; one case here goes through the chat.
@pytest.mark.parametrize(
    ("kept", "broken_rule"),
    [
        pytest.param([1, 4], "There's no scene 4: the script has 3.", id="a scene not sent"),
    ],
)
def test_a_shortened_script_that_breaks_the_rules_changes_nothing(
    fake_model: FakeModel,
    asked_about_length_with_a_pour: None,
    say: Callable[..., None],
    kept: list[int],
    broken_rule: str,
) -> None:
    before = scenes_now()
    shortening_to(
        fake_model,
        *[(scene, "Short lines fit our favourite drinks at home every day.") for scene in kept],
    )
    # Told why and asked again, the shortening breaks the same rule again.
    fake_model.respond(
        "shorten_script",
        {
            "lines": [
                {"scene": scene, "line": "Short lines fit our favourite drinks at home every day."}
                for scene in kept
            ]
        },
    )

    say("Shorten it")

    result = results_of("run_planning_checks")[1]
    assert result.startswith("Failed: a model's answer couldn't be used")
    assert broken_rule in result
    assert scenes_now() == before


# --- The rules on their own ------------------------------------------------------------------


def test_a_broll_rewrite_keeps_all_photo_needs_as_drawing_suggestions(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    needs = [{"what": f"detail {number}", "photos": [2]} for number in range(1, 5)]
    rewritten = {**REWRITTEN, "person_shown": "no face", "needs": needs}
    instructions: list[str] = []

    def rewriting(request: Any) -> dict[str, Any]:
        instructions.append(request.instructions)
        return rewritten

    checking(fake_model, product_page_url, showing(POUR))
    fake_model.respond(
        "fact_check", shows_wrong("The page doesn't mention tea.", passing=(1, 3)), facts_ok(2)
    )
    fake_model.answer_unscripted("rewrite_line", rewriting)
    say(f"Make an ad for {product_page_url}")

    scene = Job.objects.get().scenes.get(number=2)
    assert scene.needs == needs
    assert scene.person_shown == "no face"
    assert scene.fact_checked
    assert len(instructions) == 1
    told = " ".join(instructions[0].split())
    assert "Both kinds have an action: never give null shows." in told
    assert "A B-roll scene shows hands and product only, no face" in told
    assert "keep its kind's why and all its B-roll details" in told
    assert "at most 5 pictures" not in told
