"""Finding the pictures the safety filter refuses by halving (requirement 22), shared by the
clip judge and the photo picker."""

from collections.abc import Callable

from gateway.gateway import MOST_REFUSED_PROBES, refused_by_halving


def probing(refused: set[int]) -> tuple[Callable[[list[int]], bool], list[list[int]]]:
    """A filter refusing any group holding one of `refused`, and the groups it was probed with."""
    probed: list[list[int]] = []

    def refuses(group: list[int]) -> bool:
        probed.append(group)
        return bool(refused & {*group})

    return refuses, probed


def test_one_refused_picture_of_eight_is_found_in_four_probes() -> None:
    refuses, probed = probing({5})

    assert refused_by_halving(refuses, list(range(8))) == [5]
    # Halves of 8, 4 and 2, and the half not known either way once the first held it.
    assert probed == [[0, 1, 2, 3], [4, 5], [4], [6, 7]]


def test_two_refused_pictures_are_both_found() -> None:
    refuses, _ = probing({1, 6})

    assert refused_by_halving(refuses, list(range(8))) == [1, 6]


def test_probing_stops_at_the_cap_and_the_pictures_not_yet_sorted_count_as_refused() -> None:
    refuses, probed = probing(set(range(40)))

    assert refused_by_halving(refuses, list(range(40))) == list(range(40))
    assert len(probed) == MOST_REFUSED_PROBES
