"""Reading the marking script's lists out of a marked screenshot's answer."""

import json

from jobs import firecrawl

from .conftest import FIRECRAWL_MARKED


def test_an_answer_from_before_hidden_pictures_were_recorded_reads_as_all_shown() -> None:
    marks = firecrawl.marks_in(json.loads(FIRECRAWL_MARKED)["data"])

    numbered = [p for p in marks["imgs"] if isinstance(p.get("n"), int)]
    assert numbered
    assert {(p["shown"], p["vis"], p["box"]) for p in numbered} == {(True, 1.0, 0)}
    assert (marks["vids"], marks["boxes"]) == ([], [])
