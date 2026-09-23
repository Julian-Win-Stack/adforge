from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel
from gateway.models import ModelCall
from jobs import tasks
from jobs.models import Job, ProducedItem, Question
from jobs.tasks import check_plan, make_person

from .conftest import FACTS_OK, PLAN, READABLE, facts_ok

pytestmark = pytest.mark.django_db

# The mug plan's third line, and what the fact check says when it gives the wrong price.
WRONG_PRICE: dict[str, Any] = {
    "decision": "checked",
    "reason": "The price in scene 3 isn't the page's.",
    "question": None,
    "lines": [
        {"scene": 1, "verdict": "ok", "problem": None, "page_says": None},
        {"scene": 2, "verdict": "ok", "problem": None, "page_says": None},
        {
            "scene": 3,
            "verdict": "wrong",
            "problem": "The line says $19.99.",
            "page_says": "$24.00",
        },
    ],
}


def still_wrong(problem: str) -> dict[str, Any]:
    """The fact check failing scene 3 again, after it was rewritten."""
    return {
        "decision": "checked",
        "reason": "Scene 3 still doesn't match the page.",
        "question": None,
        "lines": [{"scene": 3, "verdict": "wrong", "problem": problem, "page_says": "$24.00"}],
    }


def rewrite(line: str) -> dict[str, Any]:
    return {"line": line}


def plan_with(*lines: str) -> dict[str, Any]:
    return {**PLAN, "plan": {**PLAN["plan"], "scenes": [{"line": line} for line in lines]}}


# The whole mug script, as the voice is given it to read when its speed is measured.
SCRIPT = " ".join(scene["line"] for scene in PLAN["plan"]["scenes"])


def handoffs(job_id: str, purpose: str) -> list[dict[str, Any]]:
    return list(
        ModelCall.objects.filter(job_id=job_id, purpose=purpose).values_list("handoff", flat=True)
    )


def scene_lines(api: APIClient, job_id: str) -> list[str]:
    return [scene["line"] for scene in api.get(f"/api/jobs/{job_id}/").json()["scenes"]]


def test_the_person_is_made_after_planning_and_before_the_checks(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    fake_model.words_per_second = 2.5
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)
    fake_model.respond("fact_check", FACTS_OK)

    job_id = start_job(product_page_url)

    calls = ModelCall.objects.filter(job_id=job_id).values_list("purpose", flat=True)
    assert list(calls) == [
        "check_page",
        "plan_ad",
        "draw_person",
        "design_voice",
        "measure_voice",
        "fact_check",
    ]
    portrait, voice = ProducedItem.objects.filter(job_id=job_id).order_by("kind")
    assert (portrait.kind, portrait.version, portrait.scene) == ("portrait", 1, None)
    assert (voice.kind, voice.version, voice.voice_id) == ("voice", 1, "fake-voice-1")
    # The voice read the whole script, 18 words, at the speed the fake speaks.
    assert voice.words_per_second == pytest.approx(2.5)
    assert handoffs(job_id, "measure_voice") == [
        {
            "voice_id": "fake-voice-1",
            "text": "Meet the Stoneware Mug from Kiln & Co. Hand-thrown, holds 350 ml, and "
            "dishwasher safe. Yours for $24.00.",
        }
    ]
    assert handoffs(job_id, "design_voice")[0]["description"] == (
        "A warm, relaxed woman in her thirties with a soft British accent."
    )
    assert (
        "A potter in her thirties in a linen apron, in a sunny workshop."
        in (handoffs(job_id, "draw_person")[0]["prompt"])
    )
    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "ready_to_render"
    messages = [entry["message"] for entry in job["activity"]]
    assert messages[messages.index("Planned 3 scenes") :] == [
        "Planned 3 scenes",
        "Making the person",
        "Made the person",
        "Checked the plan",
    ]


def test_a_person_half_made_when_the_worker_stopped_is_finished_without_a_second_portrait(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)
    fake_model.respond("design_voice", WorkerStopped())
    with pytest.raises(WorkerStopped):
        start_job(product_page_url)
    job = Job.objects.get()
    assert job.status == "making_person"
    assert job.produced.filter(kind="portrait").count() == 1

    # The broker hands the task out again.
    fake_model.respond("fact_check", FACTS_OK)
    make_person(str(job.pk))

    assert api.get(f"/api/jobs/{job.pk}/").json()["status"] == "ready_to_render"
    assert ModelCall.objects.filter(purpose="draw_person").count() == 1
    assert list(job.produced.values_list("kind", "version")) == [("portrait", 1), ("voice", 1)]


class WorkerStopped(BaseException):
    """The worker process dying mid-task: nothing in the job catches it."""


@pytest.mark.parametrize(
    ("kind", "purpose", "made", "stopped_at"),
    [
        ("portrait", "draw_person", "file", "design_voice"),
        ("voice", "design_voice", "voice_id", "measure_voice"),
    ],
)
def test_a_part_of_the_person_paid_for_but_not_kept_when_the_worker_stopped_is_reused(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
    kind: str,
    purpose: str,
    made: str,
    stopped_at: str,
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)
    fake_model.respond(stopped_at, WorkerStopped())
    with pytest.raises(WorkerStopped):
        start_job(product_page_url)
    job = Job.objects.get()
    # The worker stopped after the call was paid for and recorded, before what it made was kept.
    job.produced.filter(kind=kind).delete()
    (paid_for,) = ModelCall.objects.filter(purpose=purpose).values_list("output", flat=True)
    assert paid_for is not None

    fake_model.respond("fact_check", FACTS_OK)
    make_person(str(job.pk))

    assert api.get(f"/api/jobs/{job.pk}/").json()["status"] == "ready_to_render"
    assert ModelCall.objects.filter(purpose=purpose).count() == 1
    # What was paid for is kept, rather than made again.
    (kept,) = job.produced.filter(kind=kind).values_list(made, flat=True)
    assert kept == paid_for[made]


def test_a_voice_being_measured_when_the_worker_stopped_is_measured_without_designing_another(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)
    fake_model.respond("measure_voice", WorkerStopped())
    with pytest.raises(WorkerStopped):
        start_job(product_page_url)
    job = Job.objects.get()

    fake_model.respond("fact_check", FACTS_OK)
    make_person(str(job.pk))

    assert api.get(f"/api/jobs/{job.pk}/").json()["status"] == "ready_to_render"
    assert ModelCall.objects.filter(purpose="design_voice").count() == 1
    voice = job.produced.get(kind="voice")
    assert voice.voice_id == "fake-voice-1"
    assert voice.words_per_second == pytest.approx(2.0)


def test_speech_paid_for_when_the_worker_stopped_is_measured_without_speaking_again(
    api: APIClient,
    fake_model: FakeModel,
    monkeypatch: pytest.MonkeyPatch,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)

    def the_worker_stops(wav: bytes) -> float:
        raise WorkerStopped

    # The worker stopped between the speech being made and paid for, and the voice being kept.
    monkeypatch.setattr(tasks, "_seconds", the_worker_stops)
    with pytest.raises(WorkerStopped):
        start_job(product_page_url)
    job = Job.objects.get()
    assert fake_model.spoken == [SCRIPT]
    # The file store never overwrites, so speaking a second time would save a second file
    # under a suffixed key, and the voice would end up keeping that one instead.
    (paid_for,) = ModelCall.objects.filter(purpose="measure_voice").values_list("output", flat=True)
    assert paid_for == {"file": "measure_voice.wav"}
    assert job.produced.get(kind="voice").words_per_second is None

    # The broker hands the task out again.
    monkeypatch.undo()
    fake_model.respond("fact_check", FACTS_OK)
    make_person(str(job.pk))

    assert api.get(f"/api/jobs/{job.pk}/").json()["status"] == "ready_to_render"
    # The script was spoken, and paid for, once: the second run read back what it already had.
    assert fake_model.spoken == [SCRIPT]
    assert ModelCall.objects.filter(purpose="measure_voice").count() == 1
    voice = job.produced.get(kind="voice")
    assert voice.file == "measure_voice.wav"
    assert voice.words_per_second == pytest.approx(2.0)


def test_a_step_handed_out_again_after_it_finished_starts_the_next_one(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)
    # The worker stopped once the person was made, before the checks got going.
    fake_model.respond("fact_check", WorkerStopped())
    with pytest.raises(WorkerStopped):
        start_job(product_page_url)
    job = Job.objects.get()
    assert job.status == "checking_plan"

    # The broker hands the finished step out again.
    fake_model.respond("fact_check", FACTS_OK)
    make_person(str(job.pk))

    assert api.get(f"/api/jobs/{job.pk}/").json()["status"] == "ready_to_render"
    assert ModelCall.objects.filter(purpose__in=["draw_person", "design_voice"]).count() == 2


def test_a_person_that_couldnt_be_made_stops_the_job_with_the_reason(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)
    fake_model.respond("draw_person", ConnectionError("picture service down"))

    job_id = start_job(product_page_url)

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "failed"
    assert (job["activity"][-1]["message"], job["activity"][-1]["reason"]) == (
        "Something went wrong while making the person",
        "An unexpected error stopped the job; the details are in the server log.",
    )
    assert not ProducedItem.objects.filter(job_id=job_id).exists()


@pytest.mark.parametrize(
    ("scene_3", "problem", "rewritten"),
    [
        ("Yours for $19.99.", "The line says $19.99.", "Yours for $24.00."),
        ("Grab it in sage green for $24.00.", "The line names the colour.", "Yours for $24.00."),
    ],
    ids=["wrong price", "names the colour"],
)
def test_a_line_that_fails_the_fact_check_is_rewritten_and_checked_again(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
    scene_3: str,
    problem: str,
    rewritten: str,
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond(
        "plan_ad",
        plan_with("Meet the Stoneware Mug from Kiln & Co.", "Holds 350 ml.", scene_3),
    )
    wrong = {**WRONG_PRICE, "lines": [*WRONG_PRICE["lines"][:2], {**WRONG_PRICE["lines"][2]}]}
    wrong["lines"][2]["problem"] = problem
    fake_model.respond("fact_check", wrong, facts_ok(3))
    fake_model.respond("rewrite_line", rewrite(rewritten))

    job_id = start_job(product_page_url)

    assert api.get(f"/api/jobs/{job_id}/").json()["status"] == "ready_to_render"
    assert scene_lines(api, job_id) == [
        "Meet the Stoneware Mug from Kiln & Co.",
        "Holds 350 ml.",
        rewritten,
    ]
    first, second = handoffs(job_id, "fact_check")
    # The fact check is told the colour, so it can fail a line that says it.
    assert first["product_colour"] == "sage green"
    assert [line["scene"] for line in first["lines"]] == [1, 2, 3]
    # Only the rewritten line is checked again.
    assert second["lines"] == [{"scene": 3, "line": rewritten}]
    (sent,) = handoffs(job_id, "rewrite_line")
    assert (sent["scene"], sent["problems"]) == (3, [{"problem": problem, "page_says": "$24.00"}])


def test_a_line_being_rewritten_when_the_worker_stopped_is_rewritten_without_checking_it_again(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", plan_with("Meet the mug.", "Holds 350 ml.", "Yours for $19.99."))
    fake_model.respond("fact_check", WRONG_PRICE)
    fake_model.respond("rewrite_line", WorkerStopped())
    with pytest.raises(WorkerStopped):
        start_job(product_page_url)
    job = Job.objects.get()

    fake_model.respond("rewrite_line", rewrite("Yours for $24.00."))
    fake_model.respond("fact_check", facts_ok(3))
    check_plan(str(job.pk))

    assert api.get(f"/api/jobs/{job.pk}/").json()["status"] == "ready_to_render"
    assert scene_lines(api, str(job.pk))[2] == "Yours for $24.00."
    # The old line isn't checked again, so its one failure is still counted once.
    assert [len(sent["lines"]) for sent in handoffs(str(job.pk), "fact_check")] == [3, 1]
    (sent,) = handoffs(str(job.pk), "rewrite_line")
    assert sent["problems"] == [{"problem": "The line says $19.99.", "page_says": "$24.00"}]


def test_a_line_still_wrong_after_two_rewrites_is_put_to_the_user(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", plan_with("Meet the mug.", "Yours for $19.99."))
    fake_model.respond(
        "fact_check",
        {
            **WRONG_PRICE,
            "lines": [WRONG_PRICE["lines"][0], {**WRONG_PRICE["lines"][2], "scene": 2}],
        },
        {
            **still_wrong("The line says $21.00."),
            "lines": [
                {
                    "scene": 2,
                    "verdict": "wrong",
                    "problem": "The line says $21.00.",
                    "page_says": "$24.00",
                }
            ],
        },
        {
            **still_wrong("The line says $22.00."),
            "lines": [
                {
                    "scene": 2,
                    "verdict": "wrong",
                    "problem": "The line says $22.00.",
                    "page_says": "$24.00",
                }
            ],
        },
    )
    fake_model.respond("rewrite_line", rewrite("Yours for $21.00."), rewrite("Yours for $22.00."))

    job_id = start_job(product_page_url)

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "needs_answer"
    assert job["question"]["kind"] == "fact_check"
    assert job["question"]["question"] == (
        'Scene 2\'s line still fails the fact check after 2 rewrites: "Yours for $22.00." '
        "The line says $22.00. The page says: $24.00 Keep this line, or give your own?"
    )
    assert job["question"]["options"] == [
        {"value": "keep", "label": "Keep this line"},
        {"value": "own", "label": "Use my own line"},
    ]
    # The second rewrite was told both reasons the line failed, oldest first.
    assert [problem["problem"] for problem in handoffs(job_id, "rewrite_line")[1]["problems"]] == [
        "The line says $19.99.",
        "The line says $21.00.",
    ]
    assert ModelCall.objects.filter(job_id=job_id, purpose="rewrite_line").count() == 2


@pytest.fixture
def asked_about_scene_2(
    fake_model: FakeModel, product_page_url: str, start_job: Callable[..., str]
) -> str:
    """A job waiting on the user to settle scene 2, which failed after 2 rewrites."""
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", plan_with("Meet the mug.", "Yours for $19.99."))
    fails: dict[str, Any] = {
        "decision": "checked",
        "reason": "Scene 2's price isn't the page's.",
        "question": None,
        "lines": [
            {"scene": 2, "verdict": "wrong", "problem": "Wrong price.", "page_says": "$24.00"}
        ],
    }
    first = {**fails, "lines": [facts_ok(1)["lines"][0], *fails["lines"]]}
    fake_model.respond("fact_check", first, fails, fails)
    fake_model.respond("rewrite_line", rewrite("Only $19.99."), rewrite("Just $19.99."))
    job_id = start_job(product_page_url)
    assert Job.objects.get(pk=job_id).status == "needs_answer"
    return job_id


def test_a_line_the_user_keeps_is_used_as_it_is(
    api: APIClient,
    fake_model: FakeModel,
    asked_about_scene_2: str,
    answer: Callable[..., int],
) -> None:
    job_id = asked_about_scene_2

    assert answer(job_id, {"answer": "keep"}) == 202

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "ready_to_render"
    assert scene_lines(api, job_id) == ["Meet the mug.", "Just $19.99."]
    # Not checked again: the fact check was called three times, all before the question.
    assert ModelCall.objects.filter(job_id=job_id, purpose="fact_check").count() == 3


def test_a_line_the_user_types_is_used_as_written(
    api: APIClient,
    fake_model: FakeModel,
    asked_about_scene_2: str,
    answer: Callable[..., int],
) -> None:
    job_id = asked_about_scene_2

    assert answer(job_id, {"answer": "own", "line": "Yours for $24.00, this week only."}) == 202

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "ready_to_render"
    assert scene_lines(api, job_id) == ["Meet the mug.", "Yours for $24.00, this week only."]
    assert ModelCall.objects.filter(job_id=job_id, purpose="fact_check").count() == 3


@pytest.mark.parametrize(
    ("data", "error"),
    [
        ({"answer": "own"}, {"line": ["Give the line the scene should say."]}),
        ({"answer": "drop"}, {"answer": ['"drop" is not a valid choice.']}),
    ],
)
def test_a_line_choice_that_cant_be_used_is_refused_and_the_question_stays_open(
    api: APIClient,
    asked_about_scene_2: str,
    data: dict[str, str],
    error: dict[str, list[str]],
) -> None:
    job_id = asked_about_scene_2

    response = api.post(f"/api/jobs/{job_id}/answer/", data, format="json")

    assert (response.status_code, response.json()) == (400, error)
    assert api.get(f"/api/jobs/{job_id}/").json()["question"]["kind"] == "fact_check"


def test_an_unclear_page_is_asked_about_straight_away_and_checked_again_with_the_answer(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
    answer: Callable[..., int],
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)
    fake_model.respond(
        "fact_check",
        {
            "decision": "unclear",
            "reason": "The page gives two prices for the mug.",
            "question": "Is the mug $24.00 or $28.00?",
            "lines": [],
        },
        FACTS_OK,
    )
    job_id = start_job(product_page_url)
    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "needs_answer"
    assert job["question"]["kind"] == "unclear_page"
    assert job["question"]["question"] == "Is the mug $24.00 or $28.00?"
    assert job["question"]["options"] == []
    assert not ModelCall.objects.filter(purpose="rewrite_line").exists()

    assert answer(job_id, {"answer": "It's $24.00."}) == 202

    assert api.get(f"/api/jobs/{job_id}/").json()["status"] == "ready_to_render"
    assert handoffs(job_id, "fact_check")[1]["conversation"] == [
        {"by": "producer", "text": "Is the mug $24.00 or $28.00?"},
        {"by": "user", "text": "It's $24.00."},
    ]
    # Answering the check doesn't plan the ad again.
    assert ModelCall.objects.filter(job_id=job_id, purpose="plan_ad").count() == 1


# The mug plan is 18 words, which the fake voice says in 9 seconds.
@pytest.mark.parametrize("target_seconds", [8, 9, 30])
def test_a_script_no_more_than_a_second_over_its_target_fits(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
    target_seconds: int,
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)
    fake_model.respond("fact_check", FACTS_OK)

    job_id = start_job(product_page_url, target_seconds=target_seconds)

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert (job["status"], job["activity"][-1]["reason"]) == (
        "ready_to_render",
        f"Every line matches the product page, and the script fits your "
        f"{target_seconds}-second target.",
    )


def test_a_script_too_long_for_its_target_stops_and_asks_what_to_do(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)
    fake_model.respond("fact_check", FACTS_OK)

    job_id = start_job(product_page_url, target_seconds=7)

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "needs_answer"
    assert job["question"]["kind"] == "length"
    assert job["question"]["question"] == (
        "Your script runs about 9.0 seconds, 2.0 over your 7-second target. "
        "Shorten it to fit, or keep it longer?"
    )
    assert job["question"]["options"] == [
        {"value": "shorten", "label": "Shorten it to fit"},
        {"value": "keep_longer", "label": "Keep it longer"},
    ]
    assert not ModelCall.objects.filter(purpose="shorten_script").exists()


def test_a_script_without_a_target_length_goes_on_at_any_length(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    fake_model.words_per_second = 0.5  # The mug plan now takes 36 seconds to say.
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)
    fake_model.respond("fact_check", FACTS_OK)

    job_id = start_job(product_page_url)

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert (job["status"], job["activity"][-1]["reason"]) == (
        "ready_to_render",
        "Every line matches the product page.",
    )


@pytest.fixture
def asked_about_length(
    fake_model: FakeModel, product_page_url: str, start_job: Callable[..., str]
) -> str:
    """A job whose 9-second script is waiting on the user, over its 5-second target."""
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)
    fake_model.respond("fact_check", FACTS_OK)
    job_id = start_job(product_page_url, target_seconds=5)
    assert Job.objects.get(pk=job_id).status == "needs_answer"
    return job_id


def test_a_script_the_user_chooses_to_shorten_is_rewritten_and_its_new_lines_checked(
    api: APIClient,
    fake_model: FakeModel,
    asked_about_length: str,
    answer: Callable[..., int],
) -> None:
    job_id = asked_about_length
    # 12 words: 6 seconds, within a second of the target.
    fake_model.respond(
        "shorten_script",
        {"lines": ["Meet the Stoneware Mug from Kiln & Co.", "Yours for $24.00, today."]},
    )
    fake_model.respond("fact_check", facts_ok(2))

    assert answer(job_id, {"answer": "shorten"}) == 202

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "ready_to_render"
    assert scene_lines(api, job_id) == [
        "Meet the Stoneware Mug from Kiln & Co.",
        "Yours for $24.00, today.",
    ]
    (sent,) = handoffs(job_id, "shorten_script")
    # 5 seconds, plus the 1 allowed over, at 2 words a second.
    assert (sent["target_seconds"], sent["most_words"]) == (5, 12)
    # The unchanged first line passed before, so only the new second line is checked.
    assert handoffs(job_id, "fact_check")[1]["lines"] == [
        {"scene": 2, "line": "Yours for $24.00, today."}
    ]
    assert job["activity"][-1]["reason"] == (
        "Every line matches the product page, and the script fits your 5-second target."
    )


def test_a_script_still_too_long_after_two_shortenings_is_asked_about_again(
    api: APIClient,
    fake_model: FakeModel,
    asked_about_length: str,
    answer: Callable[..., int],
) -> None:
    job_id = asked_about_length
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

    assert answer(job_id, {"answer": "shorten"}) == 202

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "needs_answer"
    assert job["question"]["kind"] == "length"
    assert job["question"]["question"].startswith("Your script runs about 6.5 seconds, 1.5 over")
    assert ModelCall.objects.filter(job_id=job_id, purpose="shorten_script").count() == 2

    # Choosing to shorten again gives the producer two more tries.
    fake_model.respond(
        "shorten_script", {"lines": ["Meet the Stoneware Mug.", "Yours for $24.00."]}
    )
    fake_model.respond("fact_check", facts_ok(1, 2))
    assert answer(job_id, {"answer": "shorten"}) == 202
    assert api.get(f"/api/jobs/{job_id}/").json()["status"] == "ready_to_render"


def test_a_script_the_user_chooses_to_keep_longer_goes_on_unchanged(
    api: APIClient,
    fake_model: FakeModel,
    asked_about_length: str,
    answer: Callable[..., int],
) -> None:
    job_id = asked_about_length

    assert answer(job_id, {"answer": "keep_longer"}) == 202

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert (job["status"], job["activity"][-1]["reason"]) == (
        "ready_to_render",
        "Every line matches the product page. The script runs longer than your "
        "5-second target, as you chose.",
    )
    assert len(scene_lines(api, job_id)) == 3
    assert not ModelCall.objects.filter(purpose="shorten_script").exists()


def test_a_line_asked_about_stays_on_record_when_shortening_drops_its_scene(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
    answer: Callable[..., int],
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", plan_with("Meet the mug.", "Yours for $19.99."))
    fails: dict[str, Any] = {
        "decision": "checked",
        "reason": "Scene 2's price isn't the page's.",
        "question": None,
        "lines": [
            {"scene": 2, "verdict": "wrong", "problem": "Wrong price.", "page_says": "$24.00"}
        ],
    }
    first = {**fails, "lines": [facts_ok(1)["lines"][0], *fails["lines"]]}
    fake_model.respond("fact_check", first, fails, fails)
    fake_model.respond("rewrite_line", rewrite("Only $19.99."), rewrite("Just $19.99."))
    job_id = start_job(product_page_url, target_seconds=2)
    # 3 words and 9: 6 seconds, over the 2-second target.
    own_line = "Yours for just $24.00 today, from Kiln & Co."
    assert answer(job_id, {"answer": "own", "line": own_line}) == 202
    assert api.get(f"/api/jobs/{job_id}/").json()["question"]["kind"] == "length"
    fake_model.respond("shorten_script", {"lines": ["Meet the mug."]})

    assert answer(job_id, {"answer": "shorten"}) == 202

    assert api.get(f"/api/jobs/{job_id}/").json()["status"] == "ready_to_render"
    assert scene_lines(api, job_id) == ["Meet the mug."]
    asked = [(each.kind, each.scene_id) for each in Question.objects.filter(job_id=job_id)]
    assert asked == [("fact_check", None), ("length", None)]
