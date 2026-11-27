"""Product options (#780): the versions a page sells under one listing (two stones, four
colours, three sizes), in the page's own names, as the photo picker names them
(`Job.product_options`). Each library item and product photo records the option it shows, or
none ("") when it shows no single one (packaging, a size chart, every option together), which
is usable for every option. Once the shop owner picks an option on the plan card
(`Job.chosen_plan["product_option"]`), everything that picks product photos is offered only
that option's photos and those usable for every option (`usable`, `usable_photos`). "All", or
no option chosen, narrows nothing.

What it doesn't stop: a look frame from a page video records no option, so it stays usable
for every option, and a picture the producer makes from its own words has none either."""

from collections.abc import Sequence
from typing import Any, Literal

from django.db.models import Q, QuerySet

from .library import PictureLabel
from .models import Job, ProductPhoto

# What a picture shows when it shows no single option, as the picker and the labelling call
# answer it; kept as "".
ALL = "all"


def names(options: Sequence[str]) -> list[str]:
    """`options` as the job keeps them: each stripped, once (ignoring case), never `ALL`."""
    kept: list[str] = []
    for option in (" ".join(each.split()) for each in options):
        if option and option.casefold() != ALL and option.casefold() not in _folded(kept):
            kept.append(option)
    return kept


def _folded(options: Sequence[str]) -> list[str]:
    return [option.casefold() for option in options]


def kept_as(answered: str, options: Sequence[str]) -> str:
    """A picture's option as answered, as the library keeps it: the job's own name for it,
    or "" for `ALL`. Raises ValueError for one that is neither."""
    if answered.strip().casefold() == ALL:
        return ""
    for option in options:
        if option.casefold() == answered.strip().casefold():
            return option
    raise ValueError(f"{answered!r} isn't one of the product's options")


def answer(job: Job) -> Any:
    """The type a call's option answer for `job` is held to: one of its options or `ALL`."""
    return Literal[tuple([*job.product_options, ALL])]


def offered(job: Job) -> bool:
    """Whether the page sells more than one option, so the plan card asks which."""
    return len(job.product_options) > 1


def chosen(job: Job) -> str:
    """The option the shop owner chose for the ad, as the job names it; "" for none chosen,
    or "all" of them."""
    return named(job, (job.chosen_plan or {}).get("product_option"))


def named(job: Job, said: object) -> str:
    """The option of `job` that `said` names, ignoring case, as the job names it; "" for
    anything else, such as "all" of them or nothing."""
    if not isinstance(said, str):
        return ""
    folded = said.strip().casefold()
    return next((each for each in job.product_options if each.casefold() == folded), "")


def usable[Labelled: PictureLabel](job: Job, items: Sequence[Labelled]) -> list[Labelled]:
    """The items of `items` usable for the chosen option: its own and those of no single
    option. Every item when no option is chosen."""
    option = chosen(job)
    return [item for item in items if not option or item.option in ("", option)]


def usable_photos(job: Job, photos: QuerySet[ProductPhoto]) -> QuerySet[ProductPhoto]:
    """`photos` narrowed as `usable` narrows library items."""
    option = chosen(job)
    return photos if not option else photos.filter(Q(option="") | Q(option=option))


def shown_in(job: Job, option: str) -> bool:
    """Whether any product photo of `job` shows `option` itself: one labelled with it."""
    return job.library.filter(option=option).exists() or job.photos.filter(option=option).exists()
