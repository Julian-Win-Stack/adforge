"""A scene's references (spec #247 point 5): what its next clip is sent from the library.

The scene's reference list is its tier 2 (spec #210 point 13): the planner's scene pictures
until the producer's set_references replaces them for that scene, in the same field. Its
exclude list names library items never sent for the scene, whatever tier would take them,
and holds against the fixing agent's film_again too (Julian, Q20). A version of an item (its
product-only or recoloured picture) is that item: excluding one excludes the other, and a
pick of one is a pick of its original.

`tier_2(scene)` is the one list both cut-and-send and the fixing agent read."""

from collections.abc import Iterable, Sequence

from .library import LibraryEntry, ProductOnly, Recoloured, library_of
from .models import Job, Scene
from .planning import ScenePicture


def original_number(job: Job, number: int) -> int:
    """The library number of the original `number` is a version of, or `number` itself."""
    everything = {item.number: item for item in library_of(job)}
    return _original(everything, number)


def excluded(scene: Scene) -> set[int]:
    """The library originals never sent for `scene`, by number."""
    everything = {item.number: item for item in library_of(scene.job)}
    return {_original(everything, number) for number in scene.excluded if number in everything}


def excluded_among(scene: Scene, numbers: Iterable[int]) -> list[int]:
    """Those of `numbers` that are excluded for `scene`, or a version of an item that is, in
    the order given and each once."""
    everything = {item.number: item for item in library_of(scene.job)}
    out = excluded(scene)
    named: list[int] = []
    for number in numbers:
        if number in everything and _original(everything, number) in out and number not in named:
            named.append(number)
    return named


def tier_2(scene: Scene, picks: Sequence[ScenePicture] | None = None) -> list[ScenePicture]:
    """`scene`'s tier 2 with its exclusions applied: `picks`, by default the scene's current
    reference list, without the excluded items."""
    chosen = (
        [ScenePicture.model_validate(picture) for picture in scene.scene_pictures]
        if picks is None
        else list(picks)
    )
    out = set(excluded_among(scene, [pick.number for pick in chosen]))
    return [pick for pick in chosen if pick.number not in out]


def _original(everything: dict[int, LibraryEntry], number: int) -> int:
    item = everything.get(number)
    while item is not None and isinstance(item.source, ProductOnly | Recoloured):
        number = item.source.of
        item = everything.get(number)
    return number
