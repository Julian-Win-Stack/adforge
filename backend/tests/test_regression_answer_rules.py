"""Exact countable question rules, independent of app execution."""

from hashlib import sha256

import pytest

from evals.regression import answer_rules as rules


def test_a_missing_middle_word_is_named_and_later_words_still_match() -> None:
    assert rules.missing_words("This mug keeps your tea warm", "This mug your tea warm") == [
        "keeps"
    ]
    assert rules.missing_words("Hypervolt 2 is $99", "hypervolt two is ninety nine dollars") == []
    assert rules.missing_words("tea tea", "tea") == ["tea"]


def test_a_last_word_may_end_five_hundredths_past_the_voice_cut_only() -> None:
    assert (
        rules.last_words("s1", [{"text": "warm", "start": 3.5, "end": 4.05}], 4.0)["verdict"]
        == "pass"
    )
    assert (
        rules.last_words("s1", [{"text": "warm", "start": 3.5, "end": 4.06}], 4.0)["verdict"]
        == "fail"
    )
    assert rules.last_words("s1", [], 4.0)["verdict"] == "fail"


@pytest.mark.parametrize("gap,verdict", [(1.0, "pass"), (1.01, "fail")])
def test_dead_air_uses_the_ad_timeline_and_does_not_count_the_end_card(
    gap: float, verdict: str
) -> None:
    # s1's word is heard at 0.0..0.5. s2's cut offsets its word to 0.5 + gap.
    cuts = [
        rules.HeardCut("s1", [{"text": "hello", "start": 3.0, "end": 3.5}], 0, 0.5, 3.0, 3.5),
        rules.HeardCut(
            "s2",
            [{"text": "there", "start": 2.0, "end": 2.5}],
            0.5 + gap,
            1.0 + gap,
            2.0,
            2.5,
        ),
    ]
    assert rules.dead_air(cuts)["verdict"] == verdict


def test_silence_before_the_first_word_counts_and_missing_times_cannot_pass() -> None:
    starts_late = rules.HeardCut("s1", [{"text": "hello", "start": 1.01, "end": 1.5}], 0, 2, 0, 2)
    assert rules.dead_air([starts_late])["verdict"] == "fail"
    assert rules.dead_air([rules.HeardCut("s1", [], 0, 2, 0, 2)])["verdict"] == "fail"
    assert rules.dead_air([])["verdict"] == "fail"


def test_only_kept_photos_need_labels_and_unknown_photos_are_not_silent_passes() -> None:
    product, other, unknown = b"the exact bottle", b"another bottle", b"a new photo"
    labels = {sha256(product).hexdigest(): "this", sha256(other).hexdigest(): "not"}

    answered = rules.photo_labels(
        [("page/product.png", product), ("page/new.png", unknown)], labels
    )

    assert answered["verdict"] == "needs_label"
    assert answered["items"][1] == {
        "item": "photo-" + sha256(unknown).hexdigest(),
        "verdict": "needs_label",
        "reason": "This kept photo has not been labelled.",
        "file": "page/new.png",
    }
    assert rules.photo_labels([("page/other.png", other)], labels)["verdict"] == "fail"
    assert rules.photo_labels([("page/product.png", product)], labels)["verdict"] == "pass"


def test_a_note_may_change_touched_scenes_but_other_clip_keys_must_be_identical() -> None:
    before = {"s1": "clips/one.mp4", "s2": "clips/two.mp4", "s3": "clips/three.mp4"}
    after = {"s1": "clips/one.mp4", "s2": "clips/two-new.mp4", "s3": "clips/three-new.mp4"}
    result = rules.untouched_clips(before, after, {"s2"})
    assert result["verdict"] == "fail"
    assert [item["item"] for item in result["items"] if item["verdict"] == "fail"] == ["s3"]
    assert rules.untouched_clips(before, after, set(before))["verdict"] == "pass"
    assert rules.untouched_clips(before, {"s1": "clips/one.mp4"}, set())["verdict"] == "fail"


def test_lengths_use_the_app_plan_estimate_and_the_measured_finished_file() -> None:
    assert rules.plan_length(["hello world"], None)["verdict"] == "pass"
    assert rules.plan_length(["one " * 150], None)["verdict"] == "fail"
    assert rules.plan_length(["one " * 25], 3)["verdict"] == "fail"
    assert rules.finished_length(35.0, None)["verdict"] == "pass"
    assert rules.finished_length(35.01, None)["verdict"] == "fail"
    assert rules.finished_length(22.0, 20)["verdict"] == "pass"
    assert rules.finished_length(22.01, 20)["verdict"] == "fail"
