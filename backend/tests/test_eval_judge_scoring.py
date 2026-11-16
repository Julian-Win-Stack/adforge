"""Agreement on flaws, independent of files, models and the ad's ship decision."""

import pytest

from evals.judge.scoring import score


def test_scores_name_the_catches_misses_and_false_alarms_with_counts() -> None:
    result = score(
        [("fail", "fail")] * 4 + [("fail", "pass")] + [("pass", "fail")] + [("pass", "pass")] * 4
    )

    assert (result.caught, result.missed, result.false_alarms, result.both_passed) == (4, 1, 1, 4)
    assert result.total == 10
    assert result.agreed == 8
    assert result.recall == 0.8
    assert result.precision == 0.8
    assert result.kappa == pytest.approx(0.6)
    assert not result.trusted


def test_questions_can_earn_trust_at_the_recall_and_precision_thresholds() -> None:
    at_recall = score(
        [("fail", "fail")] * 8
        + [("fail", "pass")] * 2
        + [("pass", "fail")]
        + [("pass", "pass")] * 19
    )
    at_precision = score(
        [("fail", "fail")] * 7
        + [("fail", "pass")]
        + [("pass", "fail")] * 3
        + [("pass", "pass")] * 39
    )

    assert at_recall.trusted
    assert at_precision.trusted


def test_kappa_can_meet_its_threshold_exactly() -> None:
    result = score(
        [("fail", "fail")] * 8
        + [("fail", "pass")] * 2
        + [("pass", "fail")]
        + [("pass", "pass")] * 9
    )

    assert result.kappa == 0.7
    assert result.trusted


def test_good_kappa_cannot_make_up_for_missing_fails_or_false_alarms() -> None:
    low_recall = score([("fail", "fail")] * 7 + [("fail", "pass")] * 3 + [("pass", "pass")] * 90)
    low_precision = score([("fail", "fail")] * 6 + [("pass", "fail")] * 4 + [("pass", "pass")] * 90)

    assert low_recall.kappa is not None and low_recall.kappa > 0.7
    assert not low_recall.trusted
    assert low_precision.kappa is not None and low_precision.kappa > 0.7
    assert not low_precision.trusted


@pytest.mark.parametrize(
    ("pairs", "recall", "precision", "kappa"),
    [
        ([], None, None, None),
        ([("pass", "pass")] * 5, None, None, None),
        ([("fail", "fail")] * 5, 1.0, 1.0, None),
        ([("pass", "fail")] * 5, None, 0.0, 0.0),
        ([("fail", "pass")] * 5, 0.0, None, 0.0),
    ],
)
def test_empty_or_one_label_data_never_earns_trust(
    pairs: list[tuple[str, str]],
    recall: float | None,
    precision: float | None,
    kappa: float | None,
) -> None:
    result = score(pairs)

    assert result.recall == recall
    assert result.precision == precision
    assert result.kappa == kappa
    assert not result.trusted


def test_negative_kappa_reports_worse_agreement_than_chance() -> None:
    result = score([("fail", "pass")] * 3 + [("pass", "fail")] * 3)

    assert result.kappa == -1.0
    assert not result.trusted


@pytest.mark.parametrize("verdict", ["doesnt_apply", "unsure", ""])
def test_non_binary_verdicts_cannot_silently_become_passes(verdict: str) -> None:
    with pytest.raises(ValueError, match="pass/fail"):
        score([("pass", verdict)])
