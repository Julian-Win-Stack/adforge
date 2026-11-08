"""The code tests (`evals.code_tests`): the demo floor and the ending, counted over a finished
ad made through the chat, and the counting rules on their own."""

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from evals import code_tests
from evals.code_tests import Scene, Shipped, demo_floor, end_card, name_said
from gateway.fake import FakeModel
from jobs.models import Job, ProducedItem

from .conftest import HeldSteps
from .test_producer_assemble import assemble, finish

# Each request commits on its own, as on the real server; the counting rules need no database.
chat_test = pytest.mark.django_db(transaction=True)


@pytest.fixture
def made(fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]) -> None:
    """A finished ad of the mug plan: talking selfie, B-roll, talking propped waist-up."""
    finish(fake_model, steps, say)
    assemble(fake_model, say)


@chat_test
def test_a_finished_ad_passes_both_tests_and_the_command_says_why(
    made: None, session_id: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert code_tests.main([session_id]) == 0

    out = capsys.readouterr().out
    assert f"{session_id} [TBT]: demo floor pass: demo scene 2 (B-roll); the ending pass" in out
    assert 'the ending pass (pass: end card held 2.00 s; pass: "Kiln & Co" said in scene 1)' in out
    assert "demo floor: 1/1 pass" in out
    assert "the ending: 1/1 pass" in out


@chat_test
def test_an_ad_whose_names_are_never_heard_fails_the_ending(
    made: None, session_id: str, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    for clip in ProducedItem.objects.filter(kind="clip"):
        clip.ruling = {**(clip.ruling or {}), "word_check": {"said": "It is lovely to hold."}}
        clip.save()

    ads = tmp_path / "ads.json"
    ads.write_text(json.dumps({"mug": session_id}))

    assert code_tests.main(["--ads", str(ads)]) == 1

    out = capsys.readouterr().out
    assert "mug [TBT]: demo floor pass: demo scene 2 (B-roll); the ending FAIL" in out
    assert '"Kiln & Co" or "Stoneware Mug" never heard' in out
    assert "name said: 0/1 pass" in out


@chat_test
def test_a_session_with_no_finished_ad_fails_and_is_named(
    checked: None, session_id: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert Job.objects.get().produced.filter(kind="finished_ad").count() == 0

    assert code_tests.main([session_id]) == 1

    assert f"{session_id}: no finished ad" in capsys.readouterr().out


def ad(*scenes: tuple[str, str], heard: str | None = "", card: float | None = 2.0) -> Shipped:
    return Shipped(
        key="ad",
        scenes=[
            Scene(number, kind, style, heard)
            for number, (kind, style) in enumerate(scenes, start=1)
        ],
        brand="Hyperice",
        product_name="Hypervolt 2",
        end_card_seconds=card,
        end_card_photo=True,
    )


def test_a_talking_scene_in_the_doing_it_style_is_a_demo_and_other_styles_are_not() -> None:
    doing = ad(("talking", "selfie"), ("talking", "talking while doing it"), ("talking", ""))
    assert demo_floor(doing) == code_tests.Verdict(True, "demo scene 2 (talking while doing it)")
    # Talking all through, as a plan before talking styles had them: only B-roll counts.
    assert not demo_floor(ad(("talking", "selfie"), ("talking", ""), ("talking", ""))).passed


def test_the_end_card_must_play_its_photo_for_at_least_one_and_a_half_seconds() -> None:
    assert end_card(ad(card=1.5)).passed
    assert end_card(ad(card=1.49)) == code_tests.Verdict(False, "end card held 1.49 s, under 1.5 s")
    assert end_card(ad(card=None)) == code_tests.Verdict(False, "no end card")
    assert end_card(replace(ad(), end_card_photo=False)) == code_tests.Verdict(
        False, "end card 2.00 s with no product photo"
    )


def test_either_name_counts_ignoring_case_punctuation_and_numbers_said_as_words() -> None:
    assert name_said(ad(("talking", ""), heard="Meet the hypervolt two.")).passed
    assert name_said(ad(("talking", ""), heard="From HYPER-ICE, today.")).passed
    unheard = name_said(ad(("talking", ""), ("broll", ""), heard=None))
    assert unheard == code_tests.Verdict(
        False, '"Hyperice" or "Hypervolt 2" never heard (scene 1, 2 has no word check)'
    )
