"""How long a script and its lines may run, and when a finished scene is planned again. Plain
checks kept apart from tests/test_producer_checks.py so they run without a database."""

import pytest

from jobs.checks import most_words_in_clips, target_length_fit
from jobs.models import Scene

from .test_producer_checks import POUR, SAID_OVER

# Lines of 10, 11 and 15 words: whole clips of 5, 6 and 7 seconds.
TEN = " ".join(["word"] * 10)
ELEVEN = " ".join(["word"] * 11)
FIFTEEN = " ".join(["word"] * 15)


@pytest.mark.parametrize(
    ("lines", "seconds", "fits"),
    [
        pytest.param([TEN, ELEVEN, ELEVEN], 17, True, id="2 seconds over"),
        pytest.param([TEN, ELEVEN, FIFTEEN], 18, False, id="3 seconds over"),
        pytest.param([TEN, TEN], 10, True, id="shorter"),
    ],
)
def test_a_script_fits_a_15_second_target_when_its_whole_clips_run_up_to_2_seconds_over_it(
    lines: list[str], seconds: int, fits: bool
) -> None:
    # Each clip plays a second longer than its line, at least 5 seconds: those whole clips
    # are what plays, so they are what must fit, as for an ad with no length named.
    fit = target_length_fit(lines, 15)
    assert (fit.seconds, fit.fits) == (seconds, fits)


def test_a_script_is_shortened_to_the_words_its_clips_say_within_the_target_and_2_seconds() -> None:
    # 3 clips in 17 seconds leave 14 seconds of talking.
    assert most_words_in_clips(15 + 2, 3) == 35


@pytest.mark.parametrize(
    ("shows", "status"),
    [
        pytest.param("", "planned", id="shows something else"),
        pytest.param(POUR, "finished", id="shows the same"),
    ],
)
def test_a_finished_scene_is_planned_again_only_when_what_it_shows_changes(
    shows: str, status: str
) -> None:
    scene = Scene(line=SAID_OVER, shows=POUR, status=Scene.Status.FINISHED)

    scene.change_line(SAID_OVER, shows)

    assert scene.status == status
