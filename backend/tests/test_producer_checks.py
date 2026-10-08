"""The planning checks, driven through the chat. The producer's model is faked at the gateway
to read the page, plan the ad, make the person and call run_planning_checks, and each test
checks what the tool handed back, what was stored and what the checks' models were given."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs.checks import fits_target, most_words, most_words_in_a_line
from jobs.models import Job, Scene

from .conftest import (
    FACTS_OK,
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
    plan_with,
    results_of,
)

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)


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
    fake_model.respond("plan_ad", plan)


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


@pytest.mark.parametrize(
    ("scene_3", "problem"),
    [
        ("Yours for $19.99.", "The line says $19.99."),
        ("Grab it in sage green for $24.00.", "The line names the colour."),
    ],
    ids=["wrong price", "names the colour"],
)
def test_a_line_that_fails_the_fact_check_is_rewritten_and_checked_again(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    scene_3: str,
    problem: str,
) -> None:
    checking(
        fake_model,
        product_page_url,
        plan_with("Meet the Stoneware Mug from Kiln & Co.", "Holds 350 ml.", scene_3),
    )
    fake_model.respond("fact_check", failing(3, problem, passing=(1, 2)), facts_ok(3))
    fake_model.respond("rewrite_line", {"line": "Yours for $24.00.", "shows": None})

    say(f"Make an ad for {product_page_url}")

    assert results_of("run_planning_checks") == [
        "The checks passed. Every line matches the product page. The ad is ready to render.\n"
        "The script:\n"
        "1. Meet the Stoneware Mug from Kiln & Co.\n"
        "2. Holds 350 ml.\n"
        "3. Yours for $24.00."
    ]
    first, second = handoffs("fact_check")
    # The fact check is told the colour, so it can fail a line that says it.
    assert first["product_colour"] == "sage green"
    assert [line["scene"] for line in first["lines"]] == [1, 2, 3]
    # Only the rewritten line is checked again.
    assert second["lines"] == [
        {"scene": 3, "line": "Yours for $24.00.", "shows": None, "usage": None, "result": None}
    ]
    (sent,) = handoffs("rewrite_line")
    assert (sent["scene"], sent["problems"]) == (
        3,
        [{"wrong": "line", "problem": problem, "page_says": "$24.00"}],
    )


def test_a_line_still_wrong_after_two_rewrites_is_handed_back_to_ask_the_user_about(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    checking(fake_model, product_page_url, plan_with("Meet the mug.", "Yours for $19.99."))
    fake_model.respond(
        "fact_check",
        failing(2, "The line says $19.99.", passing=(1,)),
        failing(2, "The line says $21.00."),
        failing(2, "The line says $22.00."),
    )
    fake_model.respond(
        "rewrite_line",
        {"line": "Yours for $21.00.", "shows": None},
        {"line": "Yours for $22.00.", "shows": None},
    )

    say(f"Make an ad for {product_page_url}")

    assert results_of("run_planning_checks") == [
        'Scene 2\'s line still fails the fact check after 2 rewrites: "Yours for $22.00." '
        "The line says $22.00. The page says: $24.00 Why: The line was rewritten 2 times and "
        "still failed the fact check, so you decide: the check itself may be wrong. Ask the "
        "shop owner whether to keep this line or give their own.\n"
        "The script:\n"
        "1. Meet the mug.\n"
        "2. Yours for $22.00."
    ]
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


# The mug plan is 18 words, which the fake voice says in 9 seconds.
@pytest.mark.parametrize("target_seconds", [7, 9, 30])
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
        "Your script runs about 9.0 seconds, 3.0 over your 6-second target. Why: At the "
        "voice's measured speed the script runs 9.0 seconds, more than 2 seconds over the 6 "
        "seconds you asked for. Ask the shop owner whether to shorten it to fit, or keep it "
        "longer."
    )
    assert "shorten_script" not in paid_for()
    assert Job.objects.get().status != "ready_to_render"


@pytest.fixture
def asked_about_length(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    """A chat whose 9-second script ran over its 5-second target, and whose producer has
    asked the user whether to shorten it."""
    checking(
        fake_model,
        product_page_url,
        PLAN,
        target_seconds=5,
        reply="Your script runs 4 seconds over. Shorten it, or keep it longer?",
    )
    fake_model.respond("fact_check", FACTS_OK)
    say(f"Make a 5 second ad for {product_page_url}")
    assert "over your 5-second target" in results_of("run_planning_checks")[0]


def test_a_script_the_user_chooses_to_shorten_is_rewritten_and_its_new_lines_checked(
    fake_model: FakeModel, asked_about_length: None, say: Callable[..., None]
) -> None:
    choosing_length(fake_model, "shorten")
    # 12 words: 6 seconds, within 2 seconds of the target.
    fake_model.respond(
        "shorten_script",
        {
            "lines": [
                {"scene": 1, "line": "Meet the Stoneware Mug from Kiln & Co."},
                {"scene": 3, "line": "Yours for $24.00, today."},
            ]
        },
    )
    fake_model.respond("fact_check", facts_ok(2))

    say("Shorten it")

    assert results_of("run_planning_checks")[1] == (
        "The checks passed. Every line matches the product page, and the script fits your "
        "5-second target. The ad is ready to render.\n"
        "The script:\n"
        "1. Meet the Stoneware Mug from Kiln & Co.\n"
        "2. Yours for $24.00, today."
    )
    (sent,) = handoffs("shorten_script")
    # 5 seconds, plus the 2 allowed over, at 2 words a second.
    assert (sent["target_seconds"], sent["most_words"]) == (5, 14)
    # The unchanged first line passed before, so only the new second line is checked.
    assert handoffs("fact_check")[1]["lines"] == [
        {
            "scene": 2,
            "line": "Yours for $24.00, today.",
            "shows": None,
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
        target_seconds=5,
        reply="Your script runs 4 seconds over. Shorten it, or keep it longer?",
    )
    fake_model.respond("fact_check", FACTS_OK)
    say(f"Make a 5 second ad for {product_page_url}")
    choosing_length(fake_model, "shorten")
    fake_model.respond(
        "shorten_script",
        {
            "lines": [
                {"scene": 1, "line": "Meet the Stoneware Mug from Kiln & Co."},
                {"scene": 3, "line": "Yours for $24.00, today."},
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
    ("kept", "second_ways"),
    [
        pytest.param((1, 3, 4, 5), [False, False, True, False], id="both ways kept"),
        pytest.param((1, 2, 4, 5), [False, False, False, False], id="the first way dropped"),
    ],
)
def test_a_shortened_second_way_of_use_stays_one_while_the_first_plays_just_before_it(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    kept: tuple[int, ...],
    second_ways: list[bool],
) -> None:
    sip = {"line": "Sip hot tea from it on a cold and rainy morning.", "shows": "a hand lifts it"}
    chill = {"line": "Or fill it with ice and cold brew on a summer day.", "shows": "ice drops in"}
    scenes = [
        {"line": "Meet the Stoneware Mug from Kiln & Co."},
        {"line": "It's hand-thrown in a small studio by one potter."},
        broll(sip),
        broll({**chill, "second_way_of_use": True}),
        {"line": "Yours for $24.00."},
    ]
    checking(
        fake_model,
        product_page_url,
        a_plan_with(scenes=scenes),
        target_seconds=5,
        reply="Your script runs over. Shorten it, or keep it longer?",
    )
    fake_model.respond("fact_check", facts_ok(1, 2, 3, 4, 5))
    say(f"Make a 5 second ad for {product_page_url}")
    choosing_length(fake_model, "shorten")
    fake_model.respond(
        "shorten_script",
        {"lines": [{"scene": number, "line": scenes[number - 1]["line"]} for number in kept]},
    )

    say("Shorten it")

    assert list(Job.objects.get().scenes.values_list("second_way_of_use", flat=True)) == (
        second_ways
    )


def test_shortening_is_told_to_keep_the_hook_and_the_call_to_action(
    fake_model: FakeModel, asked_about_length: None, say: Callable[..., None]
) -> None:
    # Shortening could drop the hook or the call to action scene.
    told: list[str] = []

    def shorten(request: Any) -> dict[str, Any]:
        told.append(request.instructions)
        return {
            "lines": [
                {"scene": 1, "line": "Meet the Stoneware Mug from Kiln & Co."},
                {"scene": 3, "line": "Yours for $24.00, today."},
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
    # 16 words, then 15: 8 and 7.5 seconds, both over 7.
    fake_model.respond(
        "shorten_script",
        {
            "lines": [
                {"scene": 1, "line": "Meet the Stoneware Mug from Kiln & Co."},
                {"scene": 2, "line": "Hand-thrown, holds 350 ml, and dishwasher safe, $24.00."},
            ]
        },
        {
            "lines": [
                {"scene": 1, "line": "Meet the Stoneware Mug from Kiln & Co."},
                {"scene": 2, "line": "Hand-thrown, it holds 350 ml, for $24.00."},
            ]
        },
    )
    fake_model.respond("fact_check", facts_ok(2), facts_ok(2))

    say("Shorten it")

    assert results_of("run_planning_checks")[1].startswith(
        "Your script runs about 7.5 seconds, 2.5 over your 5-second target."
    )
    assert paid_for().count("shorten_script") == 2

    # Choosing to shorten again gives the producer two more tries.
    choosing_length(fake_model, "shorten")
    fake_model.respond(
        "shorten_script",
        {
            "lines": [
                {"scene": 1, "line": "Meet the Stoneware Mug."},
                {"scene": 2, "line": "Yours for $24.00."},
            ]
        },
    )
    fake_model.respond("fact_check", facts_ok(1, 2))
    say("Shorten it again")

    assert results_of("run_planning_checks")[2].startswith("The checks passed.")
    assert lines() == ["Meet the Stoneware Mug.", "Yours for $24.00."]


def test_a_script_the_user_chooses_to_keep_longer_goes_on_unchanged(
    fake_model: FakeModel, asked_about_length: None, say: Callable[..., None]
) -> None:
    choosing_length(fake_model, "keep_longer")

    say("Keep it longer")

    assert results_of("run_planning_checks")[1].splitlines()[0] == (
        "The checks passed. Every line matches the product page. The script runs longer than "
        "your 5-second target, as you chose. The ad is ready to render."
    )
    assert lines() == [scene["line"] for scene in PLAN["plan"]["scenes"]]
    assert "shorten_script" not in paid_for()


# --- Scenes that show something while the line is said ---------------------------------------

POUR = "tea poured from a teapot into the mug"
# Scene 2's line: its 8 words take the fake voice the 4 seconds a B-roll line takes at least.
SAID_OVER = "Hand-thrown, holds 350 ml, and dishwasher safe too."


def showing(shows: str) -> dict[str, Any]:
    """The mug plan, with its second scene showing `shows` while its line is said."""
    scenes = [
        {"line": "Meet the Stoneware Mug from Kiln & Co."},
        broll({"line": SAID_OVER, "shows": shows}),
        {"line": "Yours for $24.00."},
    ]
    return {**PLAN, "plan": {**PLAN["plan"], "scenes": scenes}}


def shows_wrong(
    problem: str, *, passing: tuple[int, ...] = (), wrong: str = "shows"
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


def test_what_a_scene_shows_is_handed_to_the_fact_check(rewrote_what_scene_2_shows: None) -> None:
    assert handoffs("fact_check")[0]["lines"] == [
        {
            "scene": 1,
            "line": "Meet the Stoneware Mug from Kiln & Co.",
            "shows": None,
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
        {"scene": 3, "line": "Yours for $24.00.", "shows": None, "usage": None, "result": None},
    ]


def test_a_scene_that_shows_something_is_fact_checked_with_the_photos_in_the_ads_colour(
    rewrote_what_scene_2_shows: None,
) -> None:
    # Photo 1 shows the mug in the ad's sage green; photo 2 shows it in cream.
    assert images_shown("fact_check") == [["Photo 1"], ["Photo 1"]]


def test_what_a_scene_shows_that_the_page_doesnt_support_is_rewritten(
    rewrote_what_scene_2_shows: None,
) -> None:
    scene = Job.objects.get().scenes.get(number=2)
    assert (scene.line, scene.shows) == (SAID_OVER, "the mug turned in a hand")


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
    # A rewrite changes one scene: it can't add a talking scene for the other steps.
    assert "name the other steps" not in instructions


def test_the_rewrite_is_told_it_was_what_the_scene_shows_that_failed(
    rewrote_what_scene_2_shows: None,
) -> None:
    assert [sent["problems"] for sent in handoffs("rewrite_line")] == [
        [
            {
                "wrong": "shows",
                "problem": "The page doesn't mention tea.",
                "page_says": "The page doesn't mention it.",
            }
        ]
    ]


def test_the_rewrite_is_handed_what_each_scene_shows(rewrote_what_scene_2_shows: None) -> None:
    assert [sent["script"] for sent in handoffs("rewrite_line")] == [
        [
            {
                "scene": 1,
                "line": "Meet the Stoneware Mug from Kiln & Co.",
                "shows": None,
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
            {"scene": 3, "line": "Yours for $24.00.", "shows": None, "usage": None, "result": None},
        ]
    ]


def test_a_rewritten_shows_is_fact_checked_again(rewrote_what_scene_2_shows: None) -> None:
    assert rechecked() == [
        [
            {
                "scene": 2,
                "line": SAID_OVER,
                "shows": "the mug turned in a hand",
                "usage": None,
                "result": None,
            }
        ]
    ]


def test_a_script_where_the_person_talks_throughout_is_checked_without_the_photos(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    checking(fake_model, product_page_url, PLAN)
    fake_model.respond("fact_check", FACTS_OK)

    say(f"Make an ad for {product_page_url}")

    assert images_shown("fact_check") == [[]]


@pytest.mark.parametrize(
    ("plan", "verdict", "told"),
    [
        pytest.param(
            PLAN,
            shows_wrong("The page doesn't mention tea.", passing=(1, 3)),
            'Scene 2 has no "shows": only its line can be wrong.',
            id="a talking scene's shows found wrong",
        ),
        pytest.param(
            PLAN,
            shows_wrong("The page doesn't mention tea.", passing=(1, 3), wrong="both"),
            'Scene 2 has no "shows": only its line can be wrong.',
            id="a talking scene's line and shows found wrong",
        ),
        pytest.param(
            showing("the mug turned in a hand"),
            {
                **FACTS_OK,
                "lines": [
                    *facts_ok(1, 3)["lines"],
                    {
                        "scene": 2,
                        "verdict": "wrong",
                        "wrong": None,
                        "problem": "Unsupported.",
                        "page_says": "Nothing.",
                    },
                ],
            },
            'A "wrong" line needs what is wrong, its problem and what the page says.',
            id="wrong without saying what",
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
    fake_model.respond("fact_check", verdict)

    say(f"Make an ad for {product_page_url}")

    (failed,) = results_of("run_planning_checks")
    assert failed.startswith("Failed: a model's answer couldn't be used")
    assert told in failed
    assert Job.objects.get().scenes.filter(fact_checked=True).count() == 0


def test_a_rewrite_never_turns_the_person_talking_into_a_scene_that_shows_something(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    checking(fake_model, product_page_url, PLAN)
    fake_model.respond("fact_check", failing(3, "The line says $19.99.", passing=(1, 2)))
    fake_model.respond("rewrite_line", broll({"line": "Yours for $24.00.", "shows": "a price tag"}))

    say(f"Make an ad for {product_page_url}")

    (failed,) = results_of("run_planning_checks")
    assert 'The person talks in this scene: its "shows" must be null.' in failed
    assert Job.objects.get().scenes.get(number=3).shows == ""


def test_a_rewrite_giving_a_talking_scene_a_blank_shows_keeps_the_person_talking(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    checking(
        fake_model,
        product_page_url,
        plan_with("Meet the Stoneware Mug from Kiln & Co.", "Holds 350 ml.", "Yours for $19.99."),
    )
    fake_model.respond(
        "fact_check", failing(3, "The line says $19.99.", passing=(1, 2)), facts_ok(3)
    )
    fake_model.respond("rewrite_line", {"line": "Yours for $24.00.", "shows": ""})

    say(f"Make an ad for {product_page_url}")

    scene = Job.objects.get().scenes.get(number=3)
    assert (scene.line, scene.shows) == ("Yours for $24.00.", "")


@pytest.mark.parametrize("shows", [None, "  "], ids=["null", "blank"])
def test_a_scene_showing_something_unsupported_can_be_rewritten_for_the_person_to_say(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], shows: str | None
) -> None:
    checking(fake_model, product_page_url, showing(POUR))
    fake_model.respond(
        "fact_check", shows_wrong("The page doesn't mention tea.", passing=(1, 3)), facts_ok(2)
    )
    fake_model.respond("rewrite_line", {"line": SAID_OVER, "shows": shows})

    say(f"Make an ad for {product_page_url}")

    scene = Job.objects.get().scenes.get(number=2)
    assert (scene.line, scene.shows) == (SAID_OVER, "")
    # A talking scene has no B-roll labels.
    assert (scene.broll_kind, scene.person_shown) == ("", "")


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
        "may be wrong. Ask the shop owner whether to keep this line and what the ad shows "
        "while it's said, give their own line, or have the person say it to camera instead."
    )
    # The shop owner is never told the kinds of scene apart.
    assert "b-roll" not in asked.lower()


@pytest.fixture
def had_the_person_say_scene_2(
    fake_model: FakeModel, asked_about_what_scene_2_shows: None, say: Callable[..., None]
) -> None:
    """A chat whose user, asked about what scene 2 shows, chose to have the person say its
    line instead."""
    say_it = {"scene": 2, "choice": "say_it", "own_line": None}
    fake_model.respond(
        "produce",
        turn(calls=[("run_planning_checks", {**NO_CHOICES, "line_choices": [say_it]})]),
        turn(says="The person will say it."),
    )
    say("Have her just say it")


def test_a_scene_the_user_has_the_person_say_shows_the_person_talking(
    had_the_person_say_scene_2: None,
) -> None:
    scene = Job.objects.get().scenes.get(number=2)
    assert (scene.line, scene.shows) == (SAID_OVER, "")


def test_a_scene_the_user_has_the_person_say_loses_its_broll_labels(
    had_the_person_say_scene_2: None,
) -> None:
    scene = Job.objects.get().scenes.get(number=2)
    assert (
        scene.broll_kind,
        scene.person_shown,
        scene.usage,
        scene.result,
        scene.needs,
    ) == ("", "", "", "", [])


def test_a_scene_the_user_has_the_person_say_goes_on_without_another_fact_check(
    had_the_person_say_scene_2: None,
) -> None:
    # The user chose: the checks pass on the 3 fact checks from before they were asked.
    assert (
        results_of("run_planning_checks")[1].splitlines()[0],
        paid_for().count("fact_check"),
    ) == ("The checks passed. Every line matches the product page. The ad is ready to render.", 3)


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
    "line": "Hand-thrown, holds 350 ml, and dishwasher safe.",
    "shows": "the mug lifted out of a dishwasher rack",
    "broll_kind": "showcase",
    "person_shown": "has face",
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
    checking(fake_model, product_page_url, doing_a_job())
    fake_model.respond(
        "fact_check",
        shows_wrong("The page doesn't say tea is poured in.", passing=(1, 3)),
        facts_ok(2),
    )
    fake_model.respond("rewrite_line", REWRITTEN)
    say(f"Make an ad for {product_page_url}")


def test_a_scenes_usage_and_result_are_handed_to_the_fact_check(
    rewrote_scene_2_whole: None,
) -> None:
    assert handoffs("fact_check")[0]["lines"][1] == {
        "scene": 2,
        "line": SAID_OVER,
        "shows": POUR,
        "usage": USAGE,
        "result": RESULT,
    }


@pytest.mark.parametrize(
    "problem",
    ["The page doesn't say tea is poured in.", "The page doesn't say the mug keeps tea hot."],
    ids=["usage", "result"],
)
def test_a_usage_or_result_the_page_doesnt_state_fails_the_scenes_fact_check(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], problem: str
) -> None:
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


def test_the_fact_check_is_shown_each_broll_scenes_needed_photos(
    rewrote_scene_2_whole: None,
) -> None:
    # Scene 2 first needed photo 2, besides photo 1 in the ad's colour; its rewrite needs
    # none.
    assert images_shown("fact_check") == [["Photo 1", "Photo 2"], ["Photo 1"]]


def test_a_rewrite_gives_back_every_broll_detail_and_they_are_stored(
    rewrote_scene_2_whole: None,
) -> None:
    scene = Job.objects.get().scenes.get(number=2)
    assert (scene.line, scene.shows) == (REWRITTEN["line"], REWRITTEN["shows"])
    assert broll_labels()[1] == ("showcase", "has face", "taken out of the dishwasher", "", [])


def test_a_rewritten_scenes_broll_details_are_fact_checked_again(
    rewrote_scene_2_whole: None,
) -> None:
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


def test_the_rewrite_is_shown_the_jobs_photos_so_it_can_give_back_what_a_scene_needs(
    rewrote_scene_2_whole: None,
) -> None:
    assert images_shown("rewrite_line") == [["Photo 1", "Photo 2"]]
    (sent,) = handoffs("rewrite_line")
    assert (sent["photo_count"], sent["colour_photos"]) == (2, [1])


def test_a_rewrite_of_a_line_the_person_says_is_shown_no_photos(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    checking(fake_model, product_page_url, a_plan_with())
    fake_model.respond(
        "fact_check", failing(3, "The line says $19.99.", passing=(1, 2)), facts_ok(3)
    )
    fake_model.respond("rewrite_line", {"line": "Yours for $24.00.", "shows": None})

    say(f"Make an ad for {product_page_url}")

    assert images_shown("rewrite_line") == [[]]


@pytest.mark.parametrize(
    ("rewrite", "told"),
    [
        pytest.param(
            {**DOES_A_JOB, "result": None},
            'A "does a job" scene needs its result: what you can see at its end.',
            id="a job without its result",
        ),
        pytest.param(
            {**DOES_A_JOB, "needs": [{"what": "the glaze up close", "photos": [3]}]},
            "There's no photo 3: the job has 2.",
            id="a photo the job doesn't have",
        ),
        pytest.param(
            {
                **DOES_A_JOB,
                "person_shown": "has face",
                "needs": [{"what": f"detail {n}", "photos": [2]} for n in range(1, 5)],
            },
            "Scene 2 would send 6 pictures (the main photo, one for each of its 4 needs and "
            "the presenter's portrait): 5 at most.",
            id="too many pictures",
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
    fake_model.respond("rewrite_line", rewrite)

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


def test_a_rewrite_with_a_blank_shows_clears_every_broll_detail(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    checking(fake_model, product_page_url, doing_a_job())
    fake_model.respond(
        "fact_check",
        shows_wrong("The page doesn't say tea is poured in.", passing=(1, 3)),
        facts_ok(2),
    )
    fake_model.respond("rewrite_line", {**DOES_A_JOB, "shows": " "})

    say(f"Make an ad for {product_page_url}")

    scene = Job.objects.get().scenes.get(number=2)
    assert (scene.shows, scene.fact_checked) == ("", True)
    assert broll_labels()[1] == ("", "", "", "", [])


def test_a_scene_the_user_has_the_person_say_loses_its_usage_result_and_needs(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    checking(
        fake_model,
        product_page_url,
        doing_a_job(),
        reply="Keep scene 2, give your own line, or say it to camera?",
    )
    fake_model.respond(
        "fact_check",
        shows_wrong("The page doesn't say tea is poured in.", passing=(1, 3)),
        shows_wrong("The page still doesn't say tea is poured in."),
        shows_wrong("The page never says tea is poured in."),
    )
    fake_model.respond("rewrite_line", DOES_A_JOB, DOES_A_JOB)
    say(f"Make an ad for {product_page_url}")
    fake_model.respond(
        "produce",
        turn(
            calls=[
                (
                    "run_planning_checks",
                    {
                        **NO_CHOICES,
                        "line_choices": [{"scene": 2, "choice": "say_it", "own_line": None}],
                    },
                )
            ]
        ),
        turn(says="The person will say it."),
    )

    say("Have her just say it")

    assert Job.objects.get().scenes.get(number=2).shows == ""
    assert broll_labels()[1] == ("", "", "", "", [])


# --- A line too long for one scene -----------------------------------------------------------


# 40 words: 20 seconds for the fake voice, over the 18 a line may take.
TOO_LONG = " ".join(["Hand-thrown and dishwasher safe, it holds 350 ml."] * 5)
# 36 words: exactly the 18 seconds a line may take.
LONGEST = " ".join(["Hand-thrown and dishwasher safe, it holds 350 ml."] * 4) + (
    " Hand-thrown, holds 350 ml."
)


# 8 words: within the 4 to 14 seconds a B-roll line takes, and a talking line's 18.
SHORTENED = "Hand-thrown and dishwasher safe, it holds 350 ml."


def scene_2_saying(line: str, *, shows: str | None = None) -> dict[str, Any]:
    """The mug plan with scene 2 saying `line`, over what it `shows` if anything."""
    plan = plan_with("Meet the Stoneware Mug from Kiln & Co.", line, "Yours for $24.00.")
    plan["plan"]["scenes"][1] = broll({**plan["plan"]["scenes"][1], "shows": shows})
    return plan


@pytest.fixture
def shortened_scene_2(
    request: pytest.FixtureRequest,
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    """A chat whose scene 2's line took 20 seconds to say, and was shortened once to fit one
    scene. Parametrize it indirectly with what scene 2 shows: nothing unless given."""
    shows: str | None = getattr(request, "param", None)
    checking(fake_model, product_page_url, scene_2_saying(TOO_LONG, shows=shows))
    fake_model.respond("fact_check", FACTS_OK, facts_ok(2))
    fake_model.respond("shorten_line", {"line": SHORTENED})
    say(f"Make an ad for {product_page_url}")


def test_a_line_too_long_for_one_scene_is_shortened(shortened_scene_2: None) -> None:
    assert lines() == [
        "Meet the Stoneware Mug from Kiln & Co.",
        SHORTENED,
        "Yours for $24.00.",
    ]


def test_a_line_is_shortened_to_the_most_words_the_voice_says_in_18_seconds(
    shortened_scene_2: None,
) -> None:
    # 18 seconds at the fake voice's 2 words a second.
    assert [(sent["scene"], sent["most_words"]) for sent in handoffs("shorten_line")] == [(2, 36)]


@pytest.mark.parametrize("shortened_scene_2", [POUR], indirect=True, ids=["showing the pour"])
def test_the_line_shortener_is_handed_what_each_scene_shows(shortened_scene_2: None) -> None:
    assert [sent["script"] for sent in handoffs("shorten_line")] == [
        [
            {
                "scene": 1,
                "line": "Meet the Stoneware Mug from Kiln & Co.",
                "shows": None,
                "usage": None,
                "result": None,
            },
            {"scene": 2, "line": TOO_LONG, "shows": POUR, "usage": None, "result": None},
            {"scene": 3, "line": "Yours for $24.00.", "shows": None, "usage": None, "result": None},
        ]
    ]


@pytest.mark.parametrize(
    ("shortened_scene_2", "shows"),
    [pytest.param(None, None, id="said to camera"), pytest.param(POUR, POUR, id="said over")],
    indirect=["shortened_scene_2"],
)
def test_a_shortened_line_is_fact_checked_again_with_what_its_scene_shows(
    shortened_scene_2: None, shows: str | None
) -> None:
    assert rechecked() == [
        [
            {
                "scene": 2,
                "line": SHORTENED,
                "shows": shows,
                "usage": None,
                "result": None,
            }
        ]
    ]


def test_a_line_said_in_exactly_18_seconds_isnt_shortened(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    checking(fake_model, product_page_url, scene_2_saying(LONGEST))
    fake_model.respond("fact_check", FACTS_OK)

    say(f"Make an ad for {product_page_url}")

    assert paid_for() == [
        "check_page",
        "copy_page_text",
        "note_face",
        "note_face",
        "plan_ad",
        "draw_person",
        "design_voice",
        "measure_voice",
        "fact_check",
    ]


@pytest.fixture
def asked_for_a_shorter_line(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    """A chat whose scene 2's line, said to camera, was still too long after 2 shortenings,
    and whose producer has asked the user for a shorter one. A B-roll line is never asked
    about (tests/test_producer_broll_line_length.py)."""
    checking(
        fake_model,
        product_page_url,
        scene_2_saying(TOO_LONG),
        reply="Scene 2 is too long. What should it say?",
    )
    fake_model.respond("fact_check", FACTS_OK, facts_ok(2), facts_ok(2))
    fake_model.respond("shorten_line", {"line": TOO_LONG}, {"line": TOO_LONG + " Really."})
    say(f"Make an ad for {product_page_url}")


def test_a_line_still_too_long_after_two_shortenings_is_asked_about(
    asked_for_a_shorter_line: None,
) -> None:
    (asked,) = results_of("run_planning_checks")
    assert asked.splitlines()[0] == (
        "Scene 2's line still takes about 20.5 seconds to say after 2 shortenings, and a scene "
        f'can last at most 18 seconds: "{TOO_LONG} Really." Why: The line was '
        "shortened 2 times and is still too long for one scene, so you choose a shorter line. "
        "Ask the shop owner for a shorter line of their own."
    )
    assert paid_for().count("shorten_line") == 2


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
        "(18 seconds at the voice's speed), so it can't be used. Ask the shop owner for a "
        "shorter line. Nothing was done."
    )


@pytest.mark.parametrize(
    "line",
    [
        pytest.param("Holds 350 ml.", id="short"),
        pytest.param(LONGEST, id="said in exactly 18 seconds"),
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
    """A chat whose 15-word, 7.5-second script, with scene 2 showing the tea poured with
    "Hand-thrown" along the top and scene 3 its price along the top, ran over its 5-second
    target, and whose producer has asked the user whether to shorten it."""
    plan = showing(POUR)
    first, pour, price = plan["plan"]["scenes"]
    plan["plan"]["scenes"] = [
        first,
        {**pour, "overlay": "Hand-thrown"},
        {**price, "overlay": "$24.00"},
    ]
    checking(fake_model, product_page_url, plan, target_seconds=5)
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    say(f"Make a 5 second ad for {product_page_url}")
    assert "over your 5-second target" in results_of("run_planning_checks")[0]


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


@pytest.fixture
def shortened_keeping_every_scene(
    fake_model: FakeModel, asked_about_length_with_a_pour: None, say: Callable[..., None]
) -> None:
    """The script above shortened to 10 words, 5 seconds, keeping every scene: the first two
    lines are new."""
    shortening_to(
        fake_model,
        (1, "Meet the Stoneware Mug."),
        (2, "Holds 350 ml."),
        (3, "Yours for $24.00."),
    )
    fake_model.respond("fact_check", facts_ok(1, 2))
    say("Shorten it")


def test_the_shortening_model_is_handed_what_each_scene_shows(
    shortened_keeping_every_scene: None,
) -> None:
    assert [sent["script"] for sent in handoffs("shorten_script")] == [
        [
            {
                "scene": 1,
                "line": "Meet the Stoneware Mug from Kiln & Co.",
                "shows": None,
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
            {"scene": 3, "line": "Yours for $24.00.", "shows": None, "usage": None, "result": None},
        ]
    ]


def test_a_shortened_line_keeps_what_its_scene_shows(shortened_keeping_every_scene: None) -> None:
    assert lines_and_shows() == [
        ("Meet the Stoneware Mug.", ""),
        ("Holds 350 ml.", POUR),
        ("Yours for $24.00.", ""),
    ]


def test_a_line_changed_in_shortening_is_fact_checked_with_what_its_scene_shows(
    shortened_keeping_every_scene: None,
) -> None:
    assert rechecked() == [
        [
            {
                "scene": 1,
                "line": "Meet the Stoneware Mug.",
                "shows": None,
                "usage": None,
                "result": None,
            },
            {"scene": 2, "line": "Holds 350 ml.", "shows": POUR, "usage": None, "result": None},
        ]
    ]


@pytest.fixture
def shortened_dropping_the_pour(
    fake_model: FakeModel, asked_about_length_with_a_pour: None, say: Callable[..., None]
) -> None:
    """The script above shortened to 11 words, 5.5 seconds, by dropping scene 2, which showed
    the tea poured."""
    shortening_to(
        fake_model, (1, "Meet the Stoneware Mug from Kiln & Co."), (3, "Yours for $24.00.")
    )
    say("Shorten it")


def test_a_scene_dropped_in_shortening_takes_what_it_shows_with_it(
    shortened_dropping_the_pour: None,
) -> None:
    # The price is said by the person, not over the pour.
    assert lines_and_shows() == [
        ("Meet the Stoneware Mug from Kiln & Co.", ""),
        ("Yours for $24.00.", ""),
    ]


def test_a_scene_dropped_in_shortening_takes_its_broll_labels_with_it(
    shortened_dropping_the_pour: None,
) -> None:
    assert broll_labels() == [("", "", "", "", []), ("", "", "", "", [])]


# A scene where the mug does a job, with the presenter's face and a need of its own.
POUR_DOES_A_JOB: dict[str, Any] = {
    "line": "Pour in hot tea and it stays warm in your hands.",
    "shows": POUR,
    "broll_kind": "does a job",
    "person_shown": "has face",
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
            {"line": "Meet the Stoneware Mug from Kiln & Co."},
            {"line": "Hand-thrown, holds 350 ml, and dishwasher safe."},
            POUR_DOES_A_JOB,
        ]
    )
    checking(fake_model, product_page_url, plan, target_seconds=5)
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    say(f"Make a 5 second ad for {product_page_url}")
    shortening_to(
        fake_model,
        (1, "Meet the Stoneware Mug."),
        (3, "Pour in hot tea and it stays warm."),
    )
    fake_model.respond("fact_check", facts_ok(1, 2))

    say("Shorten it")

    assert lines_and_shows() == [
        ("Meet the Stoneware Mug.", ""),
        ("Pour in hot tea and it stays warm.", POUR),
    ]
    assert broll_labels() == [
        ("", "", "", "", []),
        (
            "does a job",
            "has face",
            "Pour a hot drink into the mug.",
            "The mug full of steaming tea.",
            [{"what": "the handle", "photos": [2]}],
        ),
    ]


def test_a_scene_dropped_in_shortening_takes_its_overlay_with_it(
    shortened_dropping_the_pour: None,
) -> None:
    # "Hand-thrown" went with the pour, and the price kept its own overlay.
    assert list(Job.objects.get().scenes.values_list("overlay", flat=True)) == ["", "$24.00"]


def test_a_line_that_moved_but_already_passed_the_fact_check_isnt_checked_again(
    shortened_dropping_the_pour: None,
) -> None:
    assert paid_for() == [
        "check_page",
        "copy_page_text",
        "note_face",
        "note_face",
        "plan_ad",
        "draw_person",
        "design_voice",
        "measure_voice",
        "fact_check",
        "shorten_script",
    ]


def test_a_checked_line_put_over_what_another_scene_shows_is_fact_checked_again(
    fake_model: FakeModel, asked_about_length_with_a_pour: None, say: Callable[..., None]
) -> None:
    # The price passed said to camera, and is now said over the pour: 11 words, 5.5 seconds.
    shortening_to(
        fake_model, (1, "Meet the Stoneware Mug from Kiln & Co."), (2, "Yours for $24.00.")
    )
    fake_model.respond("fact_check", facts_ok(2))

    say("Shorten it")

    assert rechecked() == [
        [{"scene": 2, "line": "Yours for $24.00.", "shows": POUR, "usage": None, "result": None}]
    ]


@pytest.mark.parametrize(
    ("kept", "broken_rule"),
    [
        pytest.param(
            [2, 3],
            "The first scene is the person talking to camera",
            id="opening on a scene that shows the product",
        ),
        pytest.param([1, 3, 2], "Give the lines in the order they play", id="out of order"),
        pytest.param([1, 1], "Give the lines in the order they play", id="a scene twice"),
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
    shortening_to(fake_model, *[(scene, "Short.") for scene in kept])

    say("Shorten it")

    result = results_of("run_planning_checks")[1]
    assert result.startswith("Failed: a model's answer couldn't be used")
    assert broken_rule in result
    assert scenes_now() == before


# --- The rules on their own ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("words_per_second", "most"),
    [pytest.param(2.0, 36, id="whole words"), pytest.param(2.1, 37, id="a part word over")],
)
def test_a_line_may_have_the_whole_words_the_voice_says_within_18_seconds(
    words_per_second: float, most: int
) -> None:
    assert most_words_in_a_line(words_per_second) == most


@pytest.mark.parametrize(("seconds", "fits"), [(17.0, True), (17.1, False), (9.0, True)])
def test_a_script_fits_a_15_second_target_up_to_2_seconds_over_it(
    seconds: float, fits: bool
) -> None:
    # The guess at the ad's length misses by up to about 2 seconds either way: a script only
    # a little over is left alone, and a shorter one always fits.
    assert fits_target(seconds, 15) is fits


def test_a_script_is_shortened_to_the_words_said_within_the_target_and_2_seconds() -> None:
    assert most_words(15, 2.0) == 34


@pytest.mark.parametrize(
    ("shows", "status"),
    [
        pytest.param("", "planned", id="shows something else"),
        pytest.param(POUR, "finished", id="shows the same"),
    ],
)
def test_a_finished_scene_is_planned_again_only_when_what_it_shows_changes(
    shows: str, status: str
) -> None:
    scene = Scene(line=SAID_OVER, shows=POUR, status=Scene.Status.FINISHED)

    scene.change_line(SAID_OVER, shows)

    assert scene.status == status
