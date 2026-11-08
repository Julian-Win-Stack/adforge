"""H3 plans and length checks through the producer's chat, with paid services scripted."""

from collections.abc import Callable
from typing import Any

import pytest
from pytest_django import Settings
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from jobs.models import QUALITY_FIELDS, Job
from jobs.plan_rules import AT_LEAST_ONE_BROLL, STYLE_AND_STATE, rules_broken
from jobs.planning import BRAND_RULE

from .conftest import (
    NO_CHOICES,
    PRODUCT_PAGE,
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
    """The mug's H3 plan of `count` scenes, meeting spec #401's plan rules: talking first and
    last, B-roll in the middle but for scene 3 of a longer plan, which she talks in while
    she drinks from the mug, and each talking scene's style and product state different from
    the talking scene before it."""
    middle = [
        {
            "line": "Enjoy the Stoneware Mug at home with your favourite drink, and get "
            "yours today for just $24.00 from the shop.",
            "kind": "talking",
            "shows": f"the presenter sips tea from the mug in scene {number}",
            "talking_style": "talking while doing it",
            "product_state": "in use",
            "shows_to_lens": False,
        }
        if number == 3
        else broll(
            {
                "line": "Enjoy the Stoneware Mug at home with your favourite drink, and get "
                "yours today for just $24.00 from the shop.",
                "shows": f"a hand sets the mug down on the table in scene {number}",
                "kind_why": "The mug on the table cannot be shown at arm's length.",
            }
        )
        for number in range(3, count)
    ]
    last_in_use = count == 3
    scenes = [
        {
            "line": "Meet the Stoneware Mug, made for your morning tea and easy evenings "
            "with a drink you can enjoy at home.",
            "kind": "talking",
            "shows": "the presenter shows the mug to the lens",
            "talking_style": "selfie",
            "product_state": "out of the box",
            "shows_to_lens": True,
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
        *middle,
        {
            "line": "Enjoy the Stoneware Mug at home with your favourite drink, and get "
            "yours today for $24.00 from Kiln & Co.",
            "kind": "talking",
            "shows": "the presenter drinks from the mug"
            if last_in_use
            else f"the presenter displays the mug in scene {count}",
            "talking_style": "propped waist-up",
            # Scene 3 of a longer plan is already in use.
            "product_state": "in use" if last_in_use else "out of the box",
            "shows_to_lens": not last_in_use,
        },
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


def test_a_talking_lines_own_opinion_needs_no_source_at_the_fact_check(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    from jobs.planning import NOT_A_CLAIM, SPOKEN_LINE_RULE

    line = "Okay, I'm obsessed with this Stoneware Mug. My mornings feel better already."
    plan = h3_plan()
    plan["plan"]["scenes"][0]["line"] = line
    script_plan(fake_model, product_page_url, plan, checks=True)

    def check(request: Any) -> dict[str, Any]:
        assert NOT_A_CLAIM in request.instructions
        assert request.handoff.lines[0].line == line
        return facts_ok(1, 2, 3)

    fake_model.answer_unscripted("fact_check", check)

    say(product_page_url)

    planner = next(asked for asked in fake_model.asked if asked.purpose == "plan_ad")
    assert SPOKEN_LINE_RULE in planner.instructions
    assert results_of("run_planning_checks")[0].startswith("The checks passed.")
    assert not any(asked.purpose == "rewrite_line" for asked in fake_model.asked)


def test_a_casual_recommendation_keeps_the_price_and_brand_through_planning_checks(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    plan = h3_plan()
    closer = (
        "Honestly, I love this Stoneware Mug. Grab yours for twenty four dollars from Kiln & Co."
    )
    plan["plan"]["scenes"][-1]["line"] = closer
    script_plan(fake_model, product_page_url, plan, checks=True)
    fake_model.respond("fact_check", facts_ok(1, 2, 3))

    say(product_page_url)

    planner = next(asked for asked in fake_model.asked if asked.purpose == "plan_ad")
    assert (
        "asks the viewer to get it, said casually the way a friend recommends something"
        in planner.instructions
    )
    assert "not as an announcement and never opening with the price" in planner.instructions
    assert "says the price unless the shop owner has asked to leave it out" in planner.instructions
    assert 'never "learn more"' in planner.instructions
    assert "only if the page or the shop owner gives one" in planner.instructions
    assert len([asked for asked in fake_model.asked if asked.purpose == "plan_ad"]) == 1
    assert results_of("run_planning_checks")[0].startswith("The checks passed.")
    assert Job.objects.get().scenes.get(number=3).line == closer


def test_a_new_plan_keeps_its_quality_fields_on_the_scenes_and_the_job(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], settings: Settings
) -> None:
    plan = h3_plan(4)
    plan["plan"]["scenes"][3].update(product_state="beside its box", packaging_photo=2)
    script_plan(fake_model, product_page_url, plan)

    say(product_page_url)

    job = Job.objects.get()
    assert list(
        job.scenes.values_list("talking_style", "product_state", "shows_to_lens", "packaging_photo")
    ) == [
        ("selfie", "out of the box", True, None),
        ("", "", False, None),
        ("talking while doing it", "in use", False, None),
        ("propped waist-up", "beside its box", True, 2),
    ]
    assert (job.brand, job.end_card_line, job.end_card_price) == (
        "Kiln & Co",
        "Hand-thrown, holds 350 ml, dishwasher safe.",
        "$24.00",
    )


def test_a_plan_without_talking_styles_is_sent_back(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], settings: Settings
) -> None:
    # Ticket #403: a talking scene names its style and product state.
    bare = h3_plan()
    bare["plan"]["scenes"] = [
        {key: value for key, value in scene.items() if key not in QUALITY_FIELDS}
        for scene in bare["plan"]["scenes"]
    ]
    script_plan(fake_model, product_page_url, bare, h3_plan())

    say(product_page_url)

    job = Job.objects.get()
    requests = [asked for asked in fake_model.asked if asked.purpose == "plan_ad"]
    assert STYLE_AND_STATE in " ".join(requests[1].instructions.split())
    assert results_of("plan_ad")[0].startswith("Planned 3 scenes.")
    assert list(job.scenes.values_list("talking_style", "product_state")) == [
        ("selfie", "out of the box"),
        ("", ""),
        ("propped waist-up", "in use"),
    ]


@pytest.mark.parametrize(
    ("change", "why"),
    [
        (
            {"brand": "Acme Pottery"},
            'The brand "Acme Pottery" isn\'t in the page text. ' + BRAND_RULE,
        ),
        (
            {"last_line": "Enjoy the Stoneware Mug at home, and get yours today for $24.00."},
            'The last scene\'s line must say the brand, "Kiln & Co"',
        ),
        (
            {"end_card_line": "The only mug you will ever need."},
            "The end card line \"The only mug you will ever need.\" isn't the page's own words",
        ),
        ({"end_card_price": "$19.99"}, 'The end card price "$19.99" isn\'t on the page'),
        # The page says "350 ml", a number but never a price.
        ({"end_card_price": "$350"}, 'The end card price "$350" isn\'t on the page'),
    ],
)
def test_a_brand_or_end_card_not_from_the_page_is_sent_back(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    change: dict[str, str],
    why: str,
) -> None:
    broken = h3_plan()
    if "last_line" in change:
        broken["plan"]["scenes"][-1]["line"] = change["last_line"]
    else:
        broken["plan"].update(change)
    script_plan(fake_model, product_page_url, broken, h3_plan())

    say(product_page_url)

    job = Job.objects.get()
    assert (job.brand, job.end_card_line, job.end_card_price) == (
        "Kiln & Co",
        "Hand-thrown, holds 350 ml, dishwasher safe.",
        "$24.00",
    )
    requests = [asked for asked in fake_model.asked if asked.purpose == "plan_ad"]
    assert len(requests) == 2
    assert why in requests[1].instructions


def a_page_that_never_writes_its_brand(httpserver: HTTPServer) -> str:
    """The mug's page with its brand only in its link, as Krave Beauty's cleanser page writes
    "KraveBeauty" (#423): no brand in its title, its words or its product data."""
    page = PRODUCT_PAGE.replace("Stoneware Mug | Kiln & Co", "Stoneware Mug").replace(
        '"brand": {{"@type": "Brand", "name": "Kiln & Co"}},', ""
    )
    assert "Kiln" not in page
    side = httpserver.url_for("/cdn/mug-side.png")
    httpserver.expect_request("/products/kilnandco-stoneware-mug").respond_with_data(
        page.format(side=side), content_type="text/html; charset=utf-8"
    )
    return httpserver.url_for("/products/kilnandco-stoneware-mug")


def a_plan_with_no_brand() -> dict[str, Any]:
    """The mug's plan for a page that never writes its brand: no brand, and no brand in any
    line. The page's photos are the mug's own, served by product_page_url."""
    plan = h3_plan()
    plan["plan"]["brand"] = ""
    plan["plan"]["scenes"][-1]["line"] = (
        "Enjoy the Stoneware Mug at home with your favourite drink, and get yours today "
        "for $24.00 from the shop."
    )
    return plan


def test_a_page_that_never_writes_its_brand_plans_with_no_brand_in_the_last_line(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    link = a_page_that_never_writes_its_brand(httpserver)
    script_plan(fake_model, link, a_plan_with_no_brand())

    say(link)

    job = Job.objects.get()
    assert results_of("plan_ad")[0].startswith("Planned 3 scenes.")
    assert [asked.purpose for asked in fake_model.asked].count("plan_ad") == 1
    assert (job.brand, job.end_card_line, job.end_card_price) == (
        "",
        "Hand-thrown, holds 350 ml, dishwasher safe.",
        "$24.00",
    )
    planner = next(asked for asked in fake_model.asked if asked.purpose == "plan_ad")
    assert BRAND_RULE in " ".join(planner.instructions.split())


def test_a_brand_the_page_text_never_writes_is_sent_back_even_when_the_link_has_it(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    link = a_page_that_never_writes_its_brand(httpserver)
    guessed = h3_plan()
    guessed["plan"]["brand"] = "KilnandCo"
    script_plan(fake_model, link, guessed, a_plan_with_no_brand())

    say(link)

    requests = [asked for asked in fake_model.asked if asked.purpose == "plan_ad"]
    assert len(requests) == 2
    assert (
        f'The brand "KilnandCo" isn\'t in the page text. {BRAND_RULE}' in requests[1].instructions
    )
    assert results_of("plan_ad")[-1].startswith("Planned 3 scenes.")
    assert Job.objects.get().brand == ""


def test_the_page_words_of_the_brand_and_end_card_are_matched_however_they_are_written(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    plan = h3_plan()
    # The page writes the price "24.00 USD" in its product data and "$24.00" in its text.
    plan["plan"].update(
        brand="kiln & co", end_card_line="Hand thrown, holds 350 ml", end_card_price="$24"
    )
    script_plan(fake_model, product_page_url, plan)

    say(product_page_url)

    job = Job.objects.get()
    assert (job.brand, job.end_card_line, job.end_card_price) == (
        "kiln & co",
        "Hand thrown, holds 350 ml",
        "$24",
    )


def test_a_plan_with_five_scenes_is_refused_and_the_planner_fixes_it(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], settings: Settings
) -> None:
    script_plan(fake_model, product_page_url, h3_plan(5), h3_plan(4))

    say(product_page_url)

    assert Job.objects.get().scenes.count() == 4
    requests = [asked for asked in fake_model.asked if asked.purpose == "plan_ad"]
    assert (
        "With no target length, plan 3 or 4 scenes: the hook, 1 or 2 body scenes and the call "
        "to action." in requests[1].instructions
    )


@pytest.mark.parametrize(
    ("scene", "change", "why"),
    [
        (1, {"kind": None}, "Input should be 'talking' or 'broll'"),
        (1, {"shows": None}, "needs an action with the product"),
        (2, {"kind_why": ""}, "needs why it cannot be shown at arm's length"),
        (2, {"person_shown": "has face"}, "never shows her face"),
        (1, {"packaging_photo": 0}, "a picture's library number, from 1"),
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
def test_a_line_still_over_fourteen_seconds_returns_to_the_producer(
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
    plan["plan"]["scenes"][1]["line"] = " ".join(["tea"] * 44)
    if kind == "talking":
        # A middle scene talks only while she uses the mug, as the planner is told.
        plan["plan"]["scenes"][1].update(
            talking_style="talking while doing it", product_state="in use"
        )
        # And the ad keeps a B-roll scene: its last.
        plan["plan"]["scenes"][2] = broll(
            {
                "line": plan["plan"]["scenes"][2]["line"],
                "shows": "a hand sets the mug down on the table",
                "kind_why": "The mug on the table cannot be shown at arm's length.",
            }
        )
    script_plan(fake_model, product_page_url, plan, checks=True)
    fake_model.respond("fact_check", facts_ok(1, 2, 3), facts_ok(2), facts_ok(2), facts_ok(2))
    fake_model.respond("shorten_line", *[{"line": " ".join(["tea"] * n)} for n in (42, 41, 40)])

    say(product_page_url)

    (result,) = results_of("run_planning_checks")
    assert "after 3 shortenings" in result
    assert "at most 14 seconds" in result
    assert "Split the scene or rewrite its line with the scene tools" in result
    assert [sent["most_words"] for sent in handoffs("shorten_line")] == [39, 39, 39]
    assert Job.objects.get().scenes.get(number=2).kind == kind
    assert (
        "info",
        "Scene 2 couldn't be made as a product shot because its line is too long for a clip, "
        "so it will be said to camera instead.",
    ) not in recorded(api, session_id)


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
    fake_model.respond(
        "shorten_script",
        {
            "lines": [
                {"scene": 1, "line": "Meet the Stoneware Mug for a good cup of tea every day."},
                {"scene": 3, "line": "It is hand thrown and holds 350 ml of tea every day."},
                {
                    "scene": 4,
                    "line": "Buy the Stoneware Mug for $24.00 from Kiln & Co today online.",
                },
            ]
        },
    )
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    say(product_page_url)

    assert results_of("run_planning_checks")[-1].startswith("The checks passed.")
    assert list(Job.objects.get().scenes.values_list("kind", "kind_why")) == [
        ("talking", ""),
        ("broll", "The close view inside the mug cannot be shown at arm's length."),
        ("talking", ""),
    ]
    # 15 seconds and the 2 allowed over, less the end card's 2 seconds and 1.6 seconds for
    # each of the 4 clips, at 2.8 words a second.
    assert handoffs("shorten_script")[0]["most_words"] == 24


def test_a_fact_rewrite_keeps_a_talking_scenes_product_action_and_kind(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    settings: Settings,
) -> None:
    from jobs.planning import NOT_A_CLAIM, SPOKEN_LINE_RULE

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
        "rewrite_line",
        {
            "line": line,
            "shows": "the presenter holds the mug up to the lens",
            "shows_to_lens": True,
        },
    )

    say(product_page_url)

    rewrite = next(asked for asked in fake_model.asked if asked.purpose == "rewrite_line")
    assert SPOKEN_LINE_RULE in rewrite.instructions
    assert NOT_A_CLAIM in rewrite.instructions
    assert results_of("run_planning_checks")[0].startswith("The checks passed.")
    scene = Job.objects.get().scenes.get(number=1)
    assert (scene.kind, scene.line, scene.shows) == (
        "talking",
        line,
        "the presenter holds the mug up to the lens",
    )
    # What the rewrite gives is kept; what it leaves out stays as planned.
    assert (scene.shows_to_lens, scene.talking_style, scene.product_state) == (
        True,
        "selfie",
        "out of the box",
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
    assert [sent["fewest_words"] for sent in handoffs("lengthen_line")] == [12, 12]


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
    assert "never shows her face" in requests[1].instructions
    assert Job.objects.get().scenes.get(number=2).person_shown == "no face"
    assert results_of("run_planning_checks")[0].startswith("The checks passed.")


def test_a_no_face_scene_may_show_her_feet_through_a_plan_and_its_fact_rewrite(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    settings: Settings,
) -> None:
    """Round 12 Crocs scene 2: the rewrite, told "hands and product only", swapped feet
    walking in the clogs for a hand holding them. Any of her body but her face is no face."""
    from jobs.picture_rules import NO_FACE_RULE

    from .test_producer_checks import failing

    plan = h3_plan()
    plan["plan"]["scenes"][1]["shows"] = "her feet walk forward in the mugs as slippers"
    script_plan(fake_model, product_page_url, plan, checks=True)
    fake_model.respond(
        "fact_check", failing(2, "The page doesn't say that.", passing=(1, 3)), facts_ok(2)
    )
    walking = "her feet walk across the kitchen floor beside the mug on a low stool"
    fake_model.respond("rewrite_line", {**plan["plan"]["scenes"][1], "shows": walking})

    say(product_page_url)

    (rewrite,) = [asked for asked in fake_model.asked if asked.purpose == "rewrite_line"]
    told = " ".join(rewrite.instructions.split())
    assert NO_FACE_RULE in told
    assert "hands and product only" not in told
    assert results_of("run_planning_checks")[0].startswith("The checks passed.")
    scene = Job.objects.get().scenes.get(number=2)
    assert (scene.kind, scene.person_shown, scene.shows) == ("broll", "no face", walking)


@pytest.mark.parametrize("words", [15, 25])
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
    plan["plan"]["scenes"][-1]["line"] = "Kiln & Co " + " ".join(["tea"] * (words - 3))
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
    kept = (
        ["Stoneware Mug " + " ".join(["tea"] * 13)] * 4
        if words == 25
        else [*["Stoneware Mug " + " ".join(["tea"] * 13)] * 3, "Kiln & Co " + "tea " * 11 + "tea"]
    )
    assert list(Job.objects.get().scenes.values_list("line", flat=True)) == kept
    assert ("shorten_script" in paid_for()) is (words == 25)
    if words == 25:
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
    plan["plan"]["scenes"][-1]["line"] = "Kiln & Co " + " ".join(["tea"] * 22)
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
        scene["line"] = "Stoneware Mug " + " ".join(["tea"] * 23)
    plan["plan"]["scenes"][-1]["line"] = "Kiln & Co " + " ".join(["tea"] * 22)
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


def test_a_plan_with_no_broll_and_two_selfies_is_sent_back_and_planned_again_by_the_rules(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], settings: Settings
) -> None:
    # Ticket #403, through the chat from the product link alone: every scene talking, the
    # middle one a selfie like the opening, is sent back in the rule's words; the plan that
    # comes back keeps every rule.
    bad = h3_plan()
    bad["plan"]["scenes"][1] = {
        **bad["plan"]["scenes"][0],
        "line": bad["plan"]["scenes"][1]["line"],
        "shows": "the presenter lifts the mug beside her face",
        "product_state": "in use",
        "shows_to_lens": False,
    }
    script_plan(fake_model, product_page_url, bad, h3_plan())

    say(product_page_url)

    requests = [asked for asked in fake_model.asked if asked.purpose == "plan_ad"]
    sent_back = " ".join(requests[1].instructions.split())
    assert "The plan has no B-roll scene." in sent_back and AT_LEAST_ONE_BROLL in sent_back
    job = Job.objects.get()
    assert rules_broken([scene.plan_scene() for scene in job.scenes.order_by("number")]) == []
    assert list(job.scenes.order_by("number").values_list("kind", "talking_style")) == [
        ("talking", "selfie"),
        ("broll", ""),
        ("talking", "propped waist-up"),
    ]
