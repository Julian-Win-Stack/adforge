"""Which tests a run takes: all of them, or one CI shard of them.

Tests that commit to the database (`django_db(transaction=True)`, nearly every chat test) take
almost all of the suite's time, so they are marked `slow`, and `-m "not slow"` is the fast run.

CI splits the suite into shards (`TEST_SHARD=2/4`), each run on its own machine. Every shard
collects the same tests, so each works out the same split by itself: whole files, heaviest
first, each onto the shard with the least weight so far. A slow test weighs as much as 100
fast ones, about what they cost."""

from collections.abc import Iterable, Mapping

SLOW_WEIGHT = 100
FAST_WEIGHT = 1


def parse_shard(value: str) -> tuple[int, int]:
    """`"2/4"` is shard 2 of 4, counted from 1."""
    number, _, count = value.partition("/")
    shard, shards = int(number), int(count)
    if not 1 <= shard <= shards:
        raise ValueError(f"TEST_SHARD={value!r} is not a shard like 2/4")
    return shard, shards


def file_weights(tests: Iterable[tuple[str, bool]]) -> dict[str, int]:
    """Each file's weight, from its tests' (file, is slow) pairs."""
    weights: dict[str, int] = {}
    for file, slow in tests:
        weights[file] = weights.get(file, 0) + (SLOW_WEIGHT if slow else FAST_WEIGHT)
    return weights


def files_of_shard(weights: Mapping[str, int], shard: int, shards: int) -> set[str]:
    """The files shard `shard` of `shards` runs. Every file is in exactly one shard."""
    loads = [0] * shards
    taken: list[set[str]] = [set() for _ in range(shards)]
    for file in sorted(weights, key=lambda file: (-weights[file], file)):
        lightest = loads.index(min(loads))
        loads[lightest] += weights[file]
        taken[lightest].add(file)
    return taken[shard - 1]
