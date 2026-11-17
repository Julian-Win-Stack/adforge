"""Code answers describe a chat-created ad and can be recalculated without paid work."""

from collections.abc import Callable
from typing import Literal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from chat import session_copy
from evals.regression.answers import answer_job, note_right_scenes, note_untouched_same
from gateway.fake import FakeModel
from jobs.models import AgentFix, Job, ProducedItem

from .conftest import HeldSteps
from .test_producer_assemble import assemble, finish
from .test_session_copy import fix_scene_2


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("historical", ["none", "first_clip", "try"])
def test_a_chat_created_ad_gets_every_code_answer_and_its_clip_counts(
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
    historical: str,
) -> None:
    finish(fake_model, steps, say)
    assemble(fake_model, say)
    job = Job.objects.get()
    if historical != "none":
        # Older fixes keep their first clip separately, with a stored word failure.
        for fixing in AgentFix.objects.filter(step__scene__job=job):
            clip = fixing.tries.get(number=1).clip
            assert clip is not None
            clip.ruling = {**(clip.ruling or {}), "word_check": {"verdict": "fail"}}
            clip.save(update_fields=["ruling"])
            fixing.tries.update(passed=False)
            if historical == "first_clip":
                fixing.first_clip = clip
                fixing.save(update_fields=["first_clip"])

    # The older shared plan fixture doesn't mark exact-product photos; choose one of
    # its actual page photos to exercise the pending-label answer too.
    job.product_photos = list(job.library.values_list("number", flat=True)[:1])
    assert job.product_photos
    # The record's planned seconds cannot override the finished file's measurement.
    job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).update(seconds=99)

    answered = answer_job(job, scenario="mug", run="first")

    assert answered["scenario"] == "mug"
    assert answered["run"] == "first"
    assert set(answered["answers"]) == {
        "page_photos_this_product",
        "plan_has_demo",
        "plan_names_brand",
        "plan_fits_length",
        "clip_words_match",
        "ad_finishes",
        "ad_fits_length",
        "ad_end_card",
        "ad_brand_heard",
        "ad_last_words",
        "ad_no_dead_air",
        "note_new_ad",
        "note_right_scenes",
        "note_untouched_same",
    }
    assert answered["answers"]["ad_finishes"]["verdict"] == "pass"
    assert answered["answers"]["ad_end_card"]["verdict"] == "pass"
    assert answered["answers"]["ad_brand_heard"]["verdict"] == "pass"
    for question in (
        "plan_has_demo",
        "plan_names_brand",
        "plan_fits_length",
        "clip_words_match",
        "ad_fits_length",
        "ad_no_dead_air",
    ):
        assert answered["answers"][question]["verdict"] == "pass", answered["answers"][question]
    assert answered["answers"]["page_photos_this_product"]["verdict"] == "needs_label"
    # The ordinary test fake makes five-second clips but hears twelve words at two
    # words per second. The eval correctly catches that its last second was cut.
    assert answered["answers"]["ad_last_words"]["verdict"] == "fail"
    assert answered["answers"]["note_untouched_same"]["verdict"] == "not_reached"
    assert answered["first_try"] == {"scenes": 3, "first_passed": 3, "clips": 3}
    assert answered["cost_usd"] >= 0


@pytest.mark.django_db(transaction=True)
def test_a_missing_full_ad_fails_but_unrequested_stages_are_not_reached(checked: None) -> None:
    job = Job.objects.get()
    assert (
        answer_job(job, scenario="mug", run="failed")["answers"]["ad_finishes"]["verdict"] == "fail"
    )
    targets: tuple[tuple[Literal["plans", "pictures", "full"], Literal["full", "picture"]], ...] = (
        ("plans", "full"),
        ("pictures", "full"),
        ("full", "picture"),
    )
    for to, kind in targets:
        answered = answer_job(job, scenario="mug", run="short", to=to, kind=kind)
        assert answered["answers"]["plan_has_demo"]["verdict"] == "pass"
        for question in (
            "clip_words_match",
            "ad_finishes",
            "ad_fits_length",
            "ad_end_card",
            "ad_brand_heard",
            "ad_last_words",
            "ad_no_dead_air",
        ):
            assert answered["answers"][question]["verdict"] == "not_reached"


@pytest.mark.django_db(transaction=True)
def test_a_real_note_on_a_copy_protects_the_other_scenes(
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
) -> None:
    finish(fake_model, steps, say)
    assemble(fake_model, say)
    original = Job.objects.get()
    assert original.session is not None
    copied = session_copy.copy(original.session)
    job = copied.jobs.get()
    before = job.produced.get(kind=ProducedItem.Kind.FINISHED_AD)
    sent_at = timezone.now()
    fix_scene_2(fake_model, copied)

    def say_on_copy(text: str) -> None:
        response = api.post(f"/api/sessions/{copied.pk}/messages/", {"text": text}, format="json")
        assert response.status_code == 201

    assemble(fake_model, say_on_copy)
    after = job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).latest("version")

    untouched = note_untouched_same(job, before, after, sent_at)
    assert untouched["verdict"] == "pass"
    assert [item["item"] for item in untouched["items"]] == ["s1", "s3"]
    assert note_right_scenes([2], before, after, sent_at)["verdict"] == "pass"
    wrong = note_right_scenes([3], before, after, sent_at)
    assert wrong["verdict"] == "fail" and "no owner fix" in wrong["reason"]
    assert note_right_scenes([9], before, after, sent_at)["verdict"] == "fail"
    assert note_right_scenes([2], before, None, sent_at)["verdict"] == "fail"
    assert original.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).count() == 1
