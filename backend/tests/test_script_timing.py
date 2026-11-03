"""How long a script and its lines may run, and when a finished scene is planned again. Plain
checks kept apart from tests/test_producer_checks.py so they run without a database."""

import pytest

from jobs.checks import fits_target, most_words, most_words_in_a_line
from jobs.models import Scene

from .test_producer_checks import POUR, SAID_OVER


@pytest.mark.parametrize(
    ("words_per_second", "most"),
    [pytest.param(2.0, 36, id="whole words"), pytest.param(2.1, 37, id="a part word over")],
)
def test_a_line_may_have_the_whole_words_the_voice_says_within_18_seconds(
    words_per_second: float, most: int
) -> None:
    assert most_words_in_a_line(words_per_second) == most


@pytest.mark.parametrize(("seconds", "fits"), [(17.0, True), (17.1, False), (9.0, True)])
def test_a_script_fits_a_15_second_target_up_to_2_seconds_over_it(
    seconds: float, fits: bool
) -> None:
    # The guess at the ad's length misses by up to about 2 seconds either way: a script only
    # a little over is left alone, and a shorter one always fits.
    assert fits_target(seconds, 15) is fits


def test_a_script_is_shortened_to_the_words_said_within_the_target_and_2_seconds() -> None:
    assert most_words(15, 2.0) == 34


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
