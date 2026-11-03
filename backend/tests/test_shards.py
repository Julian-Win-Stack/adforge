import pytest

from .shards import file_weights, files_of_shard, parse_shard

WEIGHTS = {"a.py": 900, "b.py": 500, "c.py": 400, "d.py": 300, "e.py": 3, "f.py": 1}


@pytest.mark.parametrize("shards", [1, 2, 3, 4, 7])
def test_every_file_runs_in_exactly_one_shard(shards: int) -> None:
    taken = [files_of_shard(WEIGHTS, shard, shards) for shard in range(1, shards + 1)]

    assert sorted(file for files in taken for file in files) == sorted(WEIGHTS)


def test_the_heaviest_files_go_to_different_shards() -> None:
    assert [files_of_shard(WEIGHTS, shard, 2) for shard in (1, 2)] == [
        {"a.py", "d.py"},
        {"b.py", "c.py", "e.py", "f.py"},
    ]


def test_a_slow_test_weighs_a_hundred_fast_ones() -> None:
    assert file_weights([("a.py", True), ("a.py", False), ("b.py", False)]) == {
        "a.py": 101,
        "b.py": 1,
    }


def test_a_shard_is_counted_from_one() -> None:
    assert parse_shard("2/4") == (2, 4)
    for wrong in ("0/4", "5/4", "4"):
        with pytest.raises(ValueError):
            parse_shard(wrong)
