"""How long a script and its lines may run, and when a finished scene is planned again. Plain
checks kept apart from tests/test_producer_checks.py so they run without a database."""

import pytest

from jobs.checks import most_words_in_clips, target_length_fit
from jobs.models import Scene

from .test_producer_checks import POUR, SAID_OVER

# Lines of 12, 13 and 17 words: whole clips of 5, 6 and 7 seconds at 2.8 words a second.
TWELVE = " ".join(["word"] * 12)
THIRTEEN = " ".join(["word"] * 13)
SEVENTEEN = " ".join(["word"] * 17)


@pytest.mark.parametrize(
    ("lines", "seconds", "fits"),
    [
        pytest.param([TWELVE, TWELVE, TWELVE], 17, True, id="2 seconds over"),
        pytest.param([TWELVE, TWELVE, THIRTEEN], 18, False, id="3 seconds over"),
        pytest.param([TWELVE], 7, True, id="shorter"),
    ],
)
def test_a_script_fits_a_15_second_target_when_its_whole_clips_run_up_to_2_seconds_over_it(
    lines: list[str], seconds: int, fits: bool
) -> None:
    # Each clip plays 0.6 seconds longer than its line, rounded up, at least 5 seconds, and the
    # 2-second end card follows them: those are what plays, so they are what must fit, as for
    # an ad with no length named.
    fit = target_length_fit(lines, 15)
    assert (fit.seconds, fit.fits) == (seconds, fits)


def test_a_script_is_shortened_to_the_words_its_clips_say_within_the_target_and_2_seconds() -> None:
    # 3 clips and the 2-second end card in 17 seconds: 0.6 seconds after each line, and up to
    # a second each where it is rounded up, leave 10.2 seconds of talking at 2.8 words a second.
    assert most_words_in_clips(15 + 2, 3) == 28


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
