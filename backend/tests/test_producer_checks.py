"""The planning checks, driven through the chat. The producer's model is faked at the gateway
to read the page, plan the ad, make the person and call run_planning_checks, and each test
checks what the tool handed back, what was stored and what the checks' models were given."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs.models import Job

from .conftest import (
    FACTS_OK,
    NO_CHOICES,
    PLAN,
    READABLE,
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
    assert second["lines"] == [{"scene": 3, "line": "Yours for $24.00.", "shows": None}]
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
@pytest.mark.parametrize("target_seconds", [8, 9, 30])
def test_a_script_no_more_than_a_second_over_its_target_fits(
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
    checking(fake_model, product_page_url, PLAN, target_seconds=7)
    fake_model.respond("fact_check", FACTS_OK)

    say(f"Make a 7 second ad for {product_page_url}")

    (result,) = results_of("run_planning_checks")
    assert result.splitlines()[0] == (
        "Your script runs about 9.0 seconds, 2.0 over your 7-second target. Why: At the "
        "voice's measured speed the script runs 9.0 seconds, more than 1 second over the 7 "
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
    # 12 words: 6 seconds, within a second of the target.
    fake_model.respond(
        "shorten_script",
        {"lines": ["Meet the Stoneware Mug from Kiln & Co.", "Yours for $24.00, today."]},
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
    # 5 seconds, plus the 1 allowed over, at 2 words a second.
    assert (sent["target_seconds"], sent["most_words"]) == (5, 12)
    # The unchanged first line passed before, so only the new second line is checked.
    assert handoffs("fact_check")[1]["lines"] == [
        {"scene": 2, "line": "Yours for $24.00, today.", "shows": None}
    ]


def test_a_script_still_too_long_after_two_shortenings_is_asked_about_again(
    fake_model: FakeModel, asked_about_length: None, say: Callable[..., None]
) -> None:
    choosing_length(fake_model, "shorten")
    # 16 words, then 13: 8 and 6.5 seconds, both over 6.
    fake_model.respond(
        "shorten_script",
        {
            "lines": [
                "Meet the Stoneware Mug from Kiln & Co.",
                "Hand-thrown, holds 350 ml, and dishwasher safe, $24.00.",
            ]
        },
        {"lines": ["Meet the Stoneware Mug from Kiln & Co.", "Holds 350 ml for $24.00."]},
    )
    fake_model.respond("fact_check", facts_ok(2), facts_ok(2))

    say("Shorten it")

    assert results_of("run_planning_checks")[1].startswith(
        "Your script runs about 6.5 seconds, 1.5 over your 5-second target."
    )
    assert paid_for().count("shorten_script") == 2

    # Choosing to shorten again gives the producer two more tries.
    choosing_length(fake_model, "shorten")
    fake_model.respond(
        "shorten_script", {"lines": ["Meet the Stoneware Mug.", "Yours for $24.00."]}
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


def showing(shows: str) -> dict[str, Any]:
    """The mug plan, with its second scene showing `shows` while its line is said."""
    scenes = [
        {"line": "Meet the Stoneware Mug from Kiln & Co."},
        {"line": "Hand-thrown, holds 350 ml.", "shows": shows},
        {"line": "Yours for $24.00."},
    ]
    return {**PLAN, "plan": {**PLAN["plan"], "scenes": scenes}}


def shows_wrong(problem: str, *, passing: tuple[int, ...] = ()) -> dict[str, Any]:
    """What the fact check answers when what scene 2 shows isn't supported."""
    return {
        "decision": "checked",
        "reason": "Nothing supports what scene 2 shows.",
        "question": None,
        "lines": [
            *facts_ok(*passing)["lines"],
            {
                "scene": 2,
                "verdict": "wrong",
                "wrong": "shows",
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


def test_what_a_scene_shows_is_checked_with_the_photos_and_rewritten_when_unsupported(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    checking(fake_model, product_page_url, showing("tea poured from a teapot into the mug"))
    fake_model.respond(
        "fact_check", shows_wrong("The page doesn't mention tea.", passing=(1, 3)), facts_ok(2)
    )
    fake_model.respond(
        "rewrite_line", {"line": "Hand-thrown, holds 350 ml.", "shows": "the mug turned in a hand"}
    )

    say(f"Make an ad for {product_page_url}")

    assert results_of("run_planning_checks")[0].startswith(
        "The checks passed. Every line matches the product page. The ad is ready to render.\n"
        "The script:\n"
        "1. Meet the Stoneware Mug from Kiln & Co.\n"
        "2. Hand-thrown, holds 350 ml. (Shows, while the voice says it: the mug turned in a "
        "hand)\n"
    )
    first, second = handoffs("fact_check")
    assert first["lines"][1] == {
        "scene": 2,
        "line": "Hand-thrown, holds 350 ml.",
        "shows": "tea poured from a teapot into the mug",
    }
    assert second["lines"] == [
        {"scene": 2, "line": "Hand-thrown, holds 350 ml.", "shows": "the mug turned in a hand"}
    ]
    # What a scene shows may be supported by how the product looks: the photos in the ad's
    # colour are shown with it.
    assert images_shown("fact_check") == [["Photo 1"], ["Photo 1"]]
    (sent,) = handoffs("rewrite_line")
    assert sent["problems"] == [
        {
            "wrong": "shows",
            "problem": "The page doesn't mention tea.",
            "page_says": "The page doesn't mention it.",
        }
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
def test_a_fact_check_that_doesnt_say_what_is_wrong_cant_be_used(
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
    fake_model.respond("rewrite_line", {"line": "Yours for $24.00.", "shows": "a price tag"})

    say(f"Make an ad for {product_page_url}")

    (failed,) = results_of("run_planning_checks")
    assert 'The person talks in this scene: its "shows" must be null.' in failed
    assert Job.objects.get().scenes.get(number=3).shows == ""


@pytest.fixture
def asked_about_what_scene_2_shows(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    """A chat whose scene 2 shows something still unsupported after 2 rewrites, and whose
    producer has asked the user what to do about it."""
    checking(
        fake_model,
        product_page_url,
        showing("tea poured from a teapot into the mug"),
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
        {"line": "Hand-thrown, holds 350 ml.", "shows": "coffee poured into the mug"},
        {"line": "Hand-thrown, holds 350 ml.", "shows": "cocoa poured into the mug"},
    )
    say(f"Make an ad for {product_page_url}")


def test_a_scene_still_showing_the_unsupported_after_two_rewrites_is_asked_about(
    asked_about_what_scene_2_shows: None,
) -> None:
    (asked,) = results_of("run_planning_checks")
    assert asked.splitlines()[0] == (
        "Scene 2's line still fails the fact check after 2 rewrites: \"Hand-thrown, holds 350 "
        "ml.\" While it's said, the ad shows: cocoa poured into the mug. The page doesn't "
        "mention cocoa. The page says: The page doesn't mention it. Why: The line was "
        "rewritten 2 times and still failed the fact check, so you decide: the check itself "
        "may be wrong. Ask the shop owner whether to keep this line and what the ad shows "
        "while it's said, give their own line, or have the person say it to camera instead."
    )
    # The shop owner is never told the kinds of scene apart.
    assert "b-roll" not in asked.lower()


def test_a_scene_the_user_has_the_person_say_shows_the_person_talking(
    fake_model: FakeModel, asked_about_what_scene_2_shows: None, say: Callable[..., None]
) -> None:
    say_it = {"scene": 2, "choice": "say_it", "own_line": None}
    fake_model.respond(
        "produce",
        turn(calls=[("run_planning_checks", {**NO_CHOICES, "line_choices": [say_it]})]),
        turn(says="The person will say it."),
    )

    say("Have her just say it")

    assert results_of("run_planning_checks")[1].startswith("The checks passed.")
    scene = Job.objects.get().scenes.get(number=2)
    assert (scene.line, scene.shows, scene.fact_checked) == (
        "Hand-thrown, holds 350 ml.",
        "",
        True,
    )
    # The user chose: the line isn't checked again.
    assert paid_for().count("fact_check") == 3


# --- A line too long for one scene -----------------------------------------------------------


# 40 words: 20 seconds for the fake voice, over the 18 a line may take.
TOO_LONG = " ".join(["Hand-thrown and dishwasher safe, it holds 350 ml."] * 5)


def test_a_line_too_long_for_one_scene_is_shortened_and_checked_again(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    checking(
        fake_model,
        product_page_url,
        plan_with("Meet the Stoneware Mug from Kiln & Co.", TOO_LONG, "Yours for $24.00."),
    )
    fake_model.respond("fact_check", FACTS_OK, facts_ok(2))
    fake_model.respond("shorten_line", {"line": "Hand-thrown and dishwasher safe."})

    say(f"Make an ad for {product_page_url}")

    assert results_of("run_planning_checks")[0].startswith("The checks passed.")
    assert lines()[1] == "Hand-thrown and dishwasher safe."
    (sent,) = handoffs("shorten_line")
    # 18 seconds at 2 words a second.
    assert (sent["scene"], sent["most_words"]) == (2, 36)
    # The shortened line is checked again, on its own.
    assert handoffs("fact_check")[1]["lines"] == [
        {"scene": 2, "line": "Hand-thrown and dishwasher safe.", "shows": None}
    ]


@pytest.fixture
def asked_for_a_shorter_line(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    """A chat whose scene 2's line was still too long after 2 shortenings, and whose
    producer has asked the user for a shorter one."""
    checking(
        fake_model,
        product_page_url,
        plan_with("Meet the Stoneware Mug from Kiln & Co.", TOO_LONG, "Yours for $24.00."),
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
        f'can last at most 18 seconds: "{TOO_LONG} Really." Why: The line was shortened 2 '
        "times and is still too long for one scene, so you choose a shorter line. Ask the "
        "shop owner for a shorter line of their own."
    )
    assert paid_for().count("shorten_line") == 2


def test_a_line_too_long_for_one_scene_cant_be_kept_or_given(
    fake_model: FakeModel, asked_for_a_shorter_line: None, say: Callable[..., None]
) -> None:
    keep = {"scene": 2, "choice": "keep", "own_line": None}
    own = {"scene": 2, "choice": "own", "own_line": TOO_LONG}
    short = {"scene": 2, "choice": "own", "own_line": "Holds 350 ml."}
    fake_model.respond(
        "produce",
        turn(calls=[("run_planning_checks", {**NO_CHOICES, "line_choices": [keep]})]),
        turn(calls=[("run_planning_checks", {**NO_CHOICES, "line_choices": [own]})]),
        turn(calls=[("run_planning_checks", {**NO_CHOICES, "line_choices": [short]})]),
        turn(says="I've used your line."),
    )

    say(f"Hmm. {TOO_LONG} Or: Holds 350 ml.")

    _, kept, owned, used = results_of("run_planning_checks")
    too_long = (
        "Refused: the line chosen for scene 2's line takes longer to say than a scene can last "
        "(18 seconds at the voice's speed), so it can't be used. Ask the shop owner for a "
        "shorter line. Nothing was done."
    )
    assert (kept, owned) == (too_long, too_long)
    assert used.startswith("The checks passed.")
    assert lines()[1] == "Holds 350 ml."
    # The user's line is used as they wrote it: never shortened.
    assert paid_for().count("shorten_line") == 2
