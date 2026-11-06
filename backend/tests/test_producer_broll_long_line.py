"""Every long scene line is shortened before filming, with one fixed speaking speed."""

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel
from jobs.models import Job

from .conftest import facts_ok, handoffs, paid_for, recorded, results_of
from .test_h3_planning import h3_plan, script_plan
from .test_producer_broll_line_length import with_line

pytestmark = pytest.mark.django_db(transaction=True)

LONG = " ".join(["mug"] * 40)
SHORT = "This hand thrown mug holds your favourite tea for relaxed mornings at home."


@pytest.mark.parametrize("kind", ["talking", "broll"])
def test_a_long_line_uses_the_fixed_limit_and_is_fact_checked_after_shortening(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], kind: str
) -> None:
    plan = with_line(kind, LONG)
    script_plan(fake_model, product_page_url, plan, checks=True)
    fake_model.respond("fact_check", facts_ok(1, 2, 3), facts_ok(2))
    fake_model.respond("shorten_line", {"line": SHORT})

    say(product_page_url)

    scene = Job.objects.get().scenes.get(number=2)
    assert scene.line == SHORT
    assert scene.kind == kind
    assert scene.fact_checked
    assert handoffs("shorten_line")[0]["most_words"] == 39
    assert handoffs("fact_check")[-1]["lines"][0]["line"] == SHORT
    assert handoffs("fact_check")[-1]["lines"][0]["shows"] == scene.shows
    assert results_of("run_planning_checks")[-1].startswith("The checks passed.")
    assert not {"speak_line", "measure_voice", "make_broll_clip"} & set(paid_for())


@pytest.mark.parametrize("kind", ["talking", "broll"])
def test_three_unsuccessful_shortenings_return_to_the_producer_and_keep_the_scene_kind(
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    kind: str,
) -> None:
    plan = with_line(kind, LONG)
    script_plan(fake_model, product_page_url, plan, checks=True)
    fake_model.respond("fact_check", facts_ok(1, 2, 3), *[facts_ok(2)] * 3)
    fake_model.respond("shorten_line", *[{"line": LONG + " again" * n} for n in (1, 2, 3)])

    say(product_page_url)

    assert paid_for().count("shorten_line") == 3
    assert "Split the scene or rewrite its line" in results_of("run_planning_checks")[-1]
    assert Job.objects.get().scenes.get(number=2).kind == kind
    assert "make_broll_clip" not in paid_for()
    assert (
        "info",
        "Scene 2 couldn't be made as a product shot because its line is too long for a clip, "
        "so it will be said to camera instead.",
    ) not in recorded(api, session_id)


def test_a_shortening_does_not_skip_the_fact_check(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    from .test_producer_checks import failing

    plan = h3_plan()
    plan["plan"]["scenes"][1]["line"] = LONG
    script_plan(fake_model, product_page_url, plan, checks=True)
    fake_model.respond("shorten_line", {"line": SHORT})
    fake_model.respond(
        "fact_check", facts_ok(1, 2, 3), failing(2, "The page gives no such claim."), facts_ok(2)
    )
    action = plan["plan"]["scenes"][1]["shows"]
    fake_model.respond(
        "rewrite_line",
        {"line": SHORT, "shows": action, "broll_kind": "showcase", "person_shown": "no face"},
    )

    say(product_page_url)

    assert paid_for().count("fact_check") == 3
    assert paid_for().count("rewrite_line") == 1
    assert "make_broll_clip" not in paid_for()
    assert Job.objects.get().scenes.get(number=2).fact_checked


def test_a_shortened_line_survives_a_worker_restart_without_another_paid_shortening(
    fake_model: FakeModel,
    product_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    from agents.tasks import restart_dead_producers

    from .test_producer_restarts import WorkerStopped, the_producer_died

    plan = h3_plan()
    plan["plan"]["scenes"][1]["line"] = LONG
    script_plan(fake_model, product_page_url, plan, checks=True)
    fake_model.respond("shorten_line", {"line": SHORT})
    fake_model.respond("fact_check", facts_ok(1, 2, 3), WorkerStopped(), facts_ok(2))

    with pytest.raises(WorkerStopped):
        say(product_page_url)

    assert Job.objects.get().scenes.get(number=2).line == SHORT
    the_producer_died(session_id)
    restart_dead_producers()

    assert paid_for().count("shorten_line") == 1
    assert Job.objects.get().scenes.get(number=2).fact_checked
    assert results_of("run_planning_checks")[-1].startswith("The checks passed.")
    assert "make_broll_clip" not in paid_for()
