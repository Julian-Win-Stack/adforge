"""H3 plans and length checks through the producer's chat, with paid services scripted."""

from collections.abc import Callable
from typing import Any

import pytest
from pytest_django import Settings
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from jobs.models import Job

from .conftest import (
    NO_CHOICES,
    READABLE,
    a_plan_with,
    broll,
    facts_ok,
    handoffs,
    paid_for,
    recorded,
    results_of,
)

pytestmark = pytest.mark.django_db(transaction=True)


def h3_plan(count: int = 3) -> dict[str, Any]:
    scenes = [
        {
            "line": "Meet the Stoneware Mug, made for your morning tea and easy evenings "
            "with a drink you can enjoy at home.",
            "kind": "talking",
            "shows": "the presenter shows the mug to the lens",
        },
        broll(
            {
                "line": "It holds 350 ml of your favourite hot tea, with a hand thrown finish "
                "that makes each drink feel special.",
                "shows": "tea pours into the mug",
                "kind": "broll",
                "kind_why": "The close view inside the mug cannot be shown at arm's length.",
            }
        ),
        *[
            {
                "line": "Enjoy the Stoneware Mug at home with your favourite drink, and get "
                "yours today for just $24.00 from the shop.",
                "kind": "talking",
                "shows": f"the presenter displays the mug in scene {number}",
            }
            for number in range(3, count + 1)
        ],
    ]
    return a_plan_with(scenes=scenes)


def script_plan(
    fake_model: FakeModel,
    link: str,
    *plans: dict[str, Any],
    target: int | None = None,
    checks: bool = False,
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": link, "target_seconds": target})]),
        turn(calls=[("plan_ad", {})]),
        *([turn(calls=[("run_planning_checks", NO_CHOICES)])] if checks else []),
        turn(says="Here is your ad's plan."),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", *plans)


def test_a_new_plan_keeps_the_kind_and_why_of_every_scene(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], settings: Settings
) -> None:
    script_plan(fake_model, product_page_url, h3_plan())

    say(product_page_url)

    job = Job.objects.get()
    assert list(job.scenes.values_list("kind", "kind_why")) == [
        ("talking", ""),
        ("broll", "The close view inside the mug cannot be shown at arm's length."),
        ("talking", ""),
    ]
    assert results_of("plan_ad")[0].startswith("Planned 3 scenes.")


def test_a_plan_with_five_scenes_is_refused_and_the_planner_fixes_it(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], settings: Settings
) -> None:
    script_plan(fake_model, product_page_url, h3_plan(5), h3_plan(4))

    say(product_page_url)

    assert Job.objects.get().scenes.count() == 4
    requests = [asked for asked in fake_model.asked if asked.purpose == "plan_ad"]
    assert "With no target length, plan 3 or 4 scenes." in requests[1].instructions


@pytest.mark.parametrize(
    ("scene", "change", "why"),
    [
        (1, {"kind": None}, "Input should be 'talking' or 'broll'"),
        (1, {"shows": None}, "needs an action with the product"),
        (2, {"kind_why": ""}, "needs why it cannot be shown at arm's length"),
        (2, {"person_shown": "has face"}, "its face is out of frame"),
        (
            1,
            {
                "kind": "broll",
                "kind_why": "Needs a close view",
                "person_shown": "no face",
                "broll_kind": "showcase",
            },
            "The first scene is the person talking to camera",
        ),
    ],
)
def test_a_kind_that_cannot_be_filmed_is_refused_before_the_plan_is_kept(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    settings: Settings,
    scene: int,
    change: dict[str, Any],
    why: str,
) -> None:
    bad = h3_plan()
    bad["plan"]["scenes"][scene - 1].update(change)
    script_plan(fake_model, product_page_url, bad, h3_plan())

    say(product_page_url)

    requests = [asked for asked in fake_model.asked if asked.purpose == "plan_ad"]
    assert why in requests[1].instructions
    assert Job.objects.get().scenes.count() == 3


def test_an_owners_long_target_allows_more_than_four_scenes_and_needs_no_measured_voice(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], settings: Settings
) -> None:
    script_plan(fake_model, product_page_url, h3_plan(7), target=70, checks=True)
    fake_model.respond("fact_check", facts_ok(*range(1, 8)))

    say(f"Make a 70 second ad for {product_page_url}")

    job = Job.objects.get()
    assert job.scenes.count() == 7
    assert results_of("run_planning_checks")[0].startswith("The checks passed.")
    assert not {"design_voice", "measure_voice"} & set(paid_for())


@pytest.mark.parametrize("kind", ["talking", "broll"])
def test_a_line_still_over_fourteen_seconds_after_three_shortenings_is_asked_about(
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    settings: Settings,
    kind: str,
) -> None:
    plan = h3_plan()
    plan["plan"]["scenes"][1]["kind"] = kind
    plan["plan"]["scenes"][1]["line"] = " ".join(["tea"] * 40)
    script_plan(fake_model, product_page_url, plan, checks=True)
    fake_model.respond("fact_check", facts_ok(1, 2, 3), facts_ok(2), facts_ok(2), facts_ok(2))
    fake_model.respond("shorten_line", *[{"line": " ".join(["tea"] * n)} for n in (39, 38, 36)])

    say(product_page_url)

    (result,) = results_of("run_planning_checks")
    assert "after 3 shortenings" in result
    assert "at most 14 seconds" in result
    assert "Ask the shop owner for a shorter line of their own." in result
    assert [sent["most_words"] for sent in handoffs("shorten_line")] == [35, 35, 35]
    assert Job.objects.get().scenes.get(number=2).kind == kind
    assert (
        "info",
        "Scene 2 couldn't be made as a product shot because its line is too long for a clip, "
        "so it will be said to camera instead.",
    ) not in recorded(api, session_id)

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
        turn(says="Please give a shorter line."),
    )
    say("Just say it to camera.")
    assert "Input should be 'keep' or 'own'" in results_of("run_planning_checks")[-1]


def test_shortening_a_target_script_keeps_the_kinds_and_reasons_with_their_actions(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    settings: Settings,
) -> None:
    plan = h3_plan(4)
    # Dropping the middle talking scene moves B-roll scene 3 into row 2.
    plan["plan"]["scenes"][1], plan["plan"]["scenes"][2] = (
        plan["plan"]["scenes"][2],
        plan["plan"]["scenes"][1],
    )
    script_plan(fake_model, product_page_url, plan, target=15, checks=True)
    fake_model.respond("fact_check", facts_ok(1, 2, 3, 4))
    say(f"Make a 15 second ad for {product_page_url}")
    assert "whether to shorten it to fit" in results_of("run_planning_checks")[0]

    fake_model.respond(
        "produce",
        turn(calls=[("run_planning_checks", {**NO_CHOICES, "length_choice": "shorten"})]),
        turn(says="The shorter script is ready."),
    )
    fake_model.respond(
        "shorten_script",
        {
            "lines": [
                {"scene": 1, "line": "Meet the Stoneware Mug for a good cup of tea."},
                {"scene": 3, "line": "It is hand thrown and it holds 350 ml of tea."},
                {"scene": 4, "line": "Buy your Stoneware Mug today for $24.00 from the shop."},
            ]
        },
    )
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    say("Shorten it to fit.")

    assert results_of("run_planning_checks")[-1].startswith("The checks passed.")
    assert list(Job.objects.get().scenes.values_list("kind", "kind_why")) == [
        ("talking", ""),
        ("broll", "The close view inside the mug cannot be shown at arm's length."),
        ("talking", ""),
    ]
    # 15 seconds and the 2 allowed over, less two seconds for each of the 4 clips.
    assert handoffs("shorten_script")[0]["most_words"] == 22


def test_a_fact_rewrite_keeps_a_talking_scenes_product_action_and_kind(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    settings: Settings,
) -> None:
    from .test_producer_checks import failing

    script_plan(fake_model, product_page_url, h3_plan(), checks=True)
    fake_model.respond(
        "fact_check", failing(1, "The line claims too much.", passing=(2, 3)), facts_ok(1)
    )
    line = (
        "Meet the Stoneware Mug, hand thrown for your cup of tea and made for your "
        "favourite drink at home today."
    )
    fake_model.respond(
        "rewrite_line", {"line": line, "shows": "the presenter holds the mug up to the lens"}
    )

    say(product_page_url)

    assert results_of("run_planning_checks")[0].startswith("The checks passed.")
    scene = Job.objects.get().scenes.get(number=1)
    assert (scene.kind, scene.line, scene.shows) == (
        "talking",
        line,
        "the presenter holds the mug up to the lens",
    )


def test_a_line_still_under_four_seconds_after_lengthenings_is_refused(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    settings: Settings,
) -> None:
    plan = h3_plan()
    plan["plan"]["scenes"][1]["line"] = "Holds 350 ml."
    script_plan(fake_model, product_page_url, plan, checks=True)
    fake_model.respond("fact_check", facts_ok(1, 2, 3), facts_ok(2), facts_ok(2))
    fake_model.respond(
        "lengthen_line", {"line": "It holds 350 ml."}, {"line": "This mug holds 350 ml."}
    )

    say(product_page_url)

    (result,) = results_of("run_planning_checks")
    assert result.startswith("Refused:")
    assert "still shorter than 4 seconds after 2 lengthenings" in result
    assert Job.objects.get().status != Job.Status.READY_TO_RENDER
    assert [sent["fewest_words"] for sent in handoffs("lengthen_line")] == [10, 10]


def test_a_broll_fact_rewrite_with_a_face_is_refused_and_fixed(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    settings: Settings,
) -> None:
    from .test_producer_checks import failing

    plan = h3_plan()
    script_plan(fake_model, product_page_url, plan, checks=True)
    fake_model.respond(
        "fact_check", failing(2, "The action needs correction.", passing=(1, 3)), facts_ok(2)
    )
    rewritten = {**plan["plan"]["scenes"][1], "person_shown": "has face"}
    fake_model.respond("rewrite_line", rewritten, {**rewritten, "person_shown": "no face"})

    say(product_page_url)

    requests = [asked for asked in fake_model.asked if asked.purpose == "rewrite_line"]
    assert len(requests) == 2
    assert "its face is out of frame" in requests[1].instructions
    assert Job.objects.get().scenes.get(number=2).person_shown == "no face"
    assert results_of("run_planning_checks")[0].startswith("The checks passed.")


@pytest.mark.parametrize("words", [15, 20])
def test_an_ad_without_an_owner_length_is_shortened_to_whole_clips_under_35_seconds(
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    words: int,
) -> None:
    plan = h3_plan(4)
    for scene in plan["plan"]["scenes"]:
        scene["line"] = "Stoneware Mug " + " ".join(["tea"] * (words - 2))
    script_plan(fake_model, product_page_url, plan, checks=True)
    fake_model.respond("fact_check", facts_ok(1, 2, 3, 4), facts_ok(1, 2, 3, 4))
    fake_model.respond(
        "shorten_script",
        {
            "lines": [
                {"scene": number, "line": "Stoneware Mug " + " ".join(["tea"] * 13)}
                for number in range(1, 5)
            ]
        },
    )

    say(product_page_url)

    assert results_of("run_planning_checks")[-1].startswith("The checks passed.")
    assert (
        list(Job.objects.get().scenes.values_list("line", flat=True))
        == ["Stoneware Mug " + " ".join(["tea"] * 13)] * 4
    )
    assert ("shorten_script" in paid_for()) is (words == 20)
    if words == 20:
        assert any("35 seconds" in text for _, text in recorded(api, session_id))
    assert not any(
        message["role"] == "notice" and "35 seconds" in message["text"]
        for message in api.get(f"/api/sessions/{session_id}/messages/").json()
    )


def test_an_owners_length_keeps_a_plan_over_the_default_cap(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    plan = h3_plan(4)
    for scene in plan["plan"]["scenes"]:
        scene["line"] = "Stoneware Mug " + " ".join(["tea"] * 23)
    script_plan(fake_model, product_page_url, plan, target=50, checks=True)
    fake_model.respond("fact_check", facts_ok(1, 2, 3, 4))

    say(f"Make a 50 second ad for {product_page_url}")

    assert results_of("run_planning_checks")[-1].startswith("The checks passed.")
    assert "shorten_script" not in paid_for()


@pytest.mark.parametrize("bad_shortening", ["still_too_long", "drops_a_scene"])
def test_a_default_shortening_must_keep_every_scene_and_fit_the_clip_cap(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    bad_shortening: str,
) -> None:
    plan = h3_plan(4)
    for scene in plan["plan"]["scenes"]:
        scene["line"] = "Stoneware Mug " + " ".join(["tea"] * 18)
    script_plan(fake_model, product_page_url, plan, checks=True)
    fake_model.respond("fact_check", facts_ok(1, 2, 3, 4), facts_ok(1, 2, 3, 4))
    short_line = (
        "Enjoy your Stoneware Mug for hot tea and relaxed mornings at home every single day."
    )
    corrected = {"lines": [{"scene": number, "line": short_line} for number in range(1, 5)]}
    invalid = (
        {
            "lines": [
                {"scene": number, "line": scene["line"]}
                for number, scene in enumerate(plan["plan"]["scenes"], 1)
            ]
        }
        if bad_shortening == "still_too_long"
        else {"lines": corrected["lines"][:3]}
    )
    fake_model.respond("shorten_script", invalid, corrected)

    say(product_page_url)

    assert results_of("run_planning_checks")[-1].startswith("The checks passed.")
    assert list(Job.objects.get().scenes.values_list("line", flat=True)) == [short_line] * 4
    requests = [asked for asked in fake_model.asked if asked.purpose == "shorten_script"]
    assert len(requests) == 2
    assert (
        "at most 35 seconds" if bad_shortening == "still_too_long" else "Keep every scene"
    ) in requests[1].instructions
