"""The word check compares only the approved line and the clip's heard words."""

import json
from typing import Any

import pytest
from pytest_django.fixtures import Settings
from rest_framework.test import APIClient

from adforge.retry import OutsideServiceDown
from chat.models import Session
from gateway.fake import FakeModel
from gateway.types import AnswerTookTooLong, BlockedBySafetyFilter, UnusableReply
from jobs.models import Job
from jobs.word_check import check, tidy

from .conftest import handoffs, recorded


@pytest.mark.parametrize(
    ("line", "said"),
    [
        ("Only $10!", "only ten dollars"),
        ("55%", "fifty-five percent"),
        ("two-month", "two month"),
        ("HELLO, world!", "Hello world."),
    ],
)
def test_equivalent_writing_matches(line: str, said: str) -> None:
    assert tidy(line) == tidy(said)


@pytest.mark.django_db
def test_a_real_difference_is_checked_and_recorded(fake_model: FakeModel, api: APIClient) -> None:
    session = Session.objects.create()
    job = Job.objects.create(session=session, product_url="https://shop.example/product")
    fake_model.respond("check_words", {"verdict": "fail", "reason": "The price changed."})
    result = check(job, "It costs $10.", "It costs twenty dollars.")

    assert result.verdict == "fail"
    assert result.reason == "The price changed."
    assert result.said == "It costs twenty dollars."
    assert handoffs("check_words") == [
        {
            "line": "It costs $10.",
            "said": "It costs twenty dollars.",
            "product_name": "",
            "brand": "",
        }
    ]
    assert recorded(api, str(session.pk)) == [
        (
            "info",
            '{"line": "It costs $10.", "said": "It costs twenty dollars.", '
            '"verdict": "fail", "reason": "The price changed.", "settled_by": "model", '
            '"rule": null, "caption_line": "It costs $10."}',
        )
    ]


@pytest.mark.django_db
def test_a_name_said_a_little_wrong_passes_with_the_written_name_in_captions(
    fake_model: FakeModel,
) -> None:
    job = Job.objects.create(product_name="Little Green", brand="BISSELL")
    fake_model.respond(
        "check_words",
        {
            "verdict": "pass",
            "reason": "Bezel is BISSELL said a little wrong.",
            "rule": "name",
            "name_heard": "Bezel",
            "name_written": "BISSELL",
        },
    )

    result = check(job, "Get your BISSELL Little Green now.", "Get your Bezel Little Green now")

    assert (result.verdict, result.rule, result.settled_by) == ("pass", "name", "model")
    assert result.caption_line == "Get your BISSELL Little Green now"
    assert handoffs("check_words") == [
        {
            "line": "Get your BISSELL Little Green now.",
            "said": "Get your Bezel Little Green now",
            "product_name": "Little Green",
            "brand": "BISSELL",
        }
    ]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("line", "said"),
    [
        ("Only $10!", "only ten dollars"),
        ("55%", "fifty-five percent"),
        ("two-month", "two month"),
        ("HELLO, world!", "Hello world."),
    ],
)
def test_exact_match_is_recorded_without_a_model(
    fake_model: FakeModel, api: APIClient, sentry_events: list[dict[str, Any]], line: str, said: str
) -> None:
    session = Session.objects.create()
    job = Job.objects.create(session=session)
    result = check(job, line, said)

    assert result.verdict == "pass"
    assert result.settled_by == "code"
    assert handoffs("check_words") == []
    [(_, record)] = recorded(api, str(session.pk))
    assert json.loads(record) == {
        "line": line,
        "said": said,
        "verdict": "pass",
        "reason": "The tidied words match exactly.",
        "settled_by": "code",
        "rule": "exact",
        "caption_line": line,
    }
    assert sentry_events == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    "answer",
    [
        {"verdict": "pass", "reason": "The meaning is unchanged."},
        {"verdict": "fail", "reason": "The last claim is missing."},
    ],
)
def test_the_model_can_accept_or_fail_a_difference(
    fake_model: FakeModel, answer: dict[str, str]
) -> None:
    job = Job.objects.create()
    fake_model.respond("check_words", answer)
    result = check(job, "A good clean in seconds.", "A clean.")
    assert result.verdict == answer["verdict"]
    assert result.reason == answer["reason"]


@pytest.mark.django_db
@pytest.mark.parametrize("failure", ["down", "refused", "unreadable", "timeout"])
def test_no_model_answer_is_unchecked_and_recorded(
    fake_model: FakeModel, api: APIClient, failure: str, settings: Settings
) -> None:
    settings.RETRY_DELAYS_SECONDS = ()

    errors = {
        "down": OutsideServiceDown("service down"),
        "refused": BlockedBySafetyFilter("refused"),
        "unreadable": UnusableReply("no answer", input_tokens=1, output_tokens=1),
        "timeout": AnswerTookTooLong("timed out"),
    }
    session = Session.objects.create()
    job = Job.objects.create(session=session)
    fake_model.respond("check_words", errors[failure])
    result = check(job, "It costs $10.", "It costs twenty dollars.")
    assert result.verdict == "unchecked"
    assert result.settled_by == "model"
    [(_, record)] = recorded(api, str(session.pk))
    assert json.loads(record)["verdict"] == "unchecked"
    assert json.loads(record)["settled_by"] == "model"


@pytest.mark.django_db
@pytest.mark.parametrize("said", [None, "", "  "])
def test_missing_transcript_is_unchecked(fake_model: FakeModel, said: str | None) -> None:
    result = check(Job.objects.create(), "The checked line.", said)
    assert result.verdict == "unchecked"
    assert handoffs("check_words") == []


@pytest.mark.parametrize(
    ("line", "said"),
    [
        ("$10", "$20"),
        ("Buy one, get two", "Buy one"),
        ("One hundred", "One thousand"),
        ("1,2", "12"),
    ],
)
def test_tidy_keeps_real_differences(line: str, said: str) -> None:
    assert tidy(line) != tidy(said)


@pytest.mark.parametrize(
    ("line", "said"),
    [
        ("£10", "$10"),
        ("€10", "10"),
        ("-10", "10"),
        ("-$10", "$10"),
    ],
)
def test_tidy_preserves_currency_and_negative_quantities(line: str, said: str) -> None:
    assert tidy(line) != tidy(said)


def test_word_check_question_is_frozen() -> None:
    from jobs.word_check import QUESTION

    assert QUESTION == (
        "Compare the checked line with the words heard in a clip and answer one question: "
        "would a viewer hear something different from the line? Different means the viewer "
        "would learn a different fact or miss one: a name, number, price, amount, time or "
        'claim that changes, goes missing or is added; a dropped or added "not", or any '
        "other word that flips the meaning; or extra words that say something the line "
        "doesn't or make no sense, such as a garbled phrase before or after the line. An "
        "ending cut off is also different: words missing at the end, or a last word heard "
        'as only the start of the line\'s last word, such as "every" for "everywhere", even when '
        "what is left still reads as a whole sentence. Fail when a viewer would hear "
        "something different. Pass everything else: a number or price written in digits but"
        " spoken in words, or a word written differently that sounds the same, because the "
        "heard words are a transcript (exact); a small word missing or added, or a changed "
        "word ending, that leaves the meaning the same (small_slip); any name said a little"
        " wrong when it is clearly the same name (name). Fail a name that sounds like a "
        "different name or another product. For name, copy the name as heard into "
        "name_heard and its spelling in the checked line into name_written; change no other"
        " words. The product_name and brand fields give the product's own names. Judge only"
        " these two texts, not whether the checked line itself is true. Give pass or fail, "
        "the rule for a pass and a short reason."
    )


@pytest.mark.parametrize(
    ("line", "said"),
    [
        ("Contains <5% sugar", "Contains 5% sugar"),
        ("Use 1/2 cup", "Use one two cup"),
        ("Dilute 1:2", "Dilute one two"),
        ("Lasts 10-20 days", "Lasts ten twenty days"),
        ("Contains .5 grams", "Contains 5 grams"),
    ],
)
def test_tidy_preserves_symbols_that_change_claims(line: str, said: str) -> None:
    assert tidy(line) != tidy(said)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("line", "said"),
    [("Contains <5% sugar", "Contains 5% sugar"), ("Use 1/2 cup", "Use one two cup")],
)
def test_changed_numeric_claims_reach_the_model(
    fake_model: FakeModel, line: str, said: str
) -> None:
    fake_model.respond("check_words", {"verdict": "fail", "reason": "The quantity changed."})

    result = check(Job.objects.create(), line, said)

    assert result.verdict == "fail"
    assert result.settled_by == "model"
    assert handoffs("check_words") == [
        {"line": line, "said": said, "product_name": "", "brand": ""}
    ]


@pytest.mark.parametrize(
    ("line", "said", "answer", "caption_line"),
    [
        (
            "Try this product today.",
            "Try this products today.",
            {"verdict": "pass", "reason": "An unnoticed inflection.", "rule": "small_slip"},
            "Try this products today.",
        ),
        (
            "Try Garject today.",
            "Try Guardject today.",
            {
                "verdict": "pass",
                "reason": "The product name sounds slightly different.",
                "rule": "name",
                "name_heard": "Guardject",
                "name_written": "Garject",
            },
            "Try Garject today.",
        ),
        (
            "Only $49.99 today.",
            "Only $39.99 today.",
            {"verdict": "fail", "reason": "The price changed."},
            "Only $49.99 today.",
        ),
        (
            "Try this product today.",
            "Try product today.",
            {"verdict": "fail", "reason": "A word is missing."},
            "Try this product today.",
        ),
    ],
)
def test_parsed_word_rule_selects_the_caption_words(
    line: str, said: str, answer: dict[str, str], caption_line: str
) -> None:
    from jobs.word_check import Answer, caption_words

    assert caption_words(line, said, Answer.model_validate(answer)) == caption_line


@pytest.mark.parametrize(
    ("line", "said", "heard", "written"),
    [
        (
            "Get the Revlon One-Step Volumizer today.",
            "Get the Evlon One-Step Volumizer today.",
            "Evlon",
            "Revlon",
        ),
        (
            "It comes with a BlenderBall whisk.",
            "It comes with a Blender Bowl whisk.",
            "Blender Bowl",
            "BlenderBall",
        ),
    ],
)
def test_any_name_said_a_little_wrong_is_captioned_as_written(
    line: str, said: str, heard: str, written: str
) -> None:
    from jobs.word_check import Answer, caption_words

    answer = Answer(
        verdict="pass",
        reason="The same name, said a little wrong.",
        rule="name",
        name_heard=heard,
        name_written=written,
    )
    assert caption_words(line, said, answer) == line


@pytest.mark.parametrize(
    ("line", "said", "heard_name", "written_name"),
    [
        ("Try Garject for $49.99.", "Try Guardject for $39.99.", "Guardject", "Otherbrand"),
        ("Try Garject for $49.99.", "Try Guardject for $39.99.", "$39.99", "$49.99"),
        ("Try Garject for $49.99.", "Try Guardject for $39.99.", "not heard", "Garject"),
        ("Try Garject for $49.99.", "Try Guardject for $39.99.", "", "Garject"),
        ("Save thirty dollars today.", "Save twenty dollars today.", "twenty", "thirty"),
        ("Ten minutes is all it takes.", "Five minutes is all it takes.", "Five", "Ten"),
        ("It is not sticky at all.", "It is sticky at all.", "is", "is not"),
        ("Dry it everywhere.", "Dry it every", "every", "everywhere"),
        ("Clean it in seconds.", "Clean it on seconds.", "on", "in"),
    ],
)
def test_name_captions_cannot_rewrite_a_number_a_not_or_a_name_not_in_the_line(
    line: str, said: str, heard_name: str, written_name: str
) -> None:
    from jobs.word_check import Answer, caption_words

    answer = Answer(
        verdict="pass",
        reason="A supposed name correction.",
        rule="name",
        name_heard=heard_name,
        name_written=written_name,
    )
    with pytest.raises(ValueError, match="name correction"):
        caption_words(line, said, answer)


def test_an_answer_saved_under_the_old_rule_name_still_reads() -> None:
    from jobs.word_check import Answer

    answer = Answer.model_validate(
        {"verdict": "pass", "reason": "Same name.", "rule": "product_name"}
    )
    assert answer.rule == "name"


@pytest.mark.parametrize(
    ("line", "said", "prefix"),
    [
        ("Try the mug today.", "Your tour. Try the mug today.", 2),
        ("Try the mug today.", "Try the mug today. Try the mug today.", 4),
        ("It costs $49.99 today.", "A false start. It costs $39.99 today.", 3),
        ("Get your mug today.", "Get your mug-- get your mug today.", 3),
        ("Try the mug today.", "Try the lovely mug today.", 0),
        ("Try the mug today.", "Try the mug today. Thank you.", 0),
        ("Try the mug today.", "A false start. Try the lovely mug today.", 0),
        ("Try the mug today.", "Try the mug today.", 0),
    ],
)
def test_only_words_before_the_line_can_be_cut(line: str, said: str, prefix: int) -> None:
    from jobs.word_check import leading_words

    assert leading_words(line, said.split()) == prefix
