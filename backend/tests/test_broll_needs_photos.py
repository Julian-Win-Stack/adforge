"""Which photos of what a B-roll scene needs go to the picture model: the rules alone,
without a chat. Driven through the chat in test_producer_broll_needs.py."""

import pytest

from jobs.scenes import NeedPhoto, photos_for_needs

# Photos 3 and 5 show a stranger's face; 4 and 6 don't.
FACES = {1: False, 3: True, 4: False, 5: True, 6: False}


@pytest.mark.parametrize(
    ("photos", "sent"),
    [
        pytest.param([3, 4], 4, id="the first without a face"),
        pytest.param([4, 6], 4, id="the first of several without a face"),
        pytest.param([5, 3], 5, id="all with a face: the first"),
        pytest.param([9, 3], 3, id="a photo the job doesn't have is passed over"),
    ],
)
def test_a_need_is_shown_by_its_first_photo_without_a_face(photos: list[int], sent: int) -> None:
    (need,) = photos_for_needs([{"what": "the gel", "photos": photos}], FACES, most=3)
    assert need.photo == sent


def test_a_photo_two_needs_share_is_sent_once_for_both() -> None:
    needs = [{"what": "the gel", "photos": [4]}, {"what": "the foam", "photos": [3, 4]}]
    assert photos_for_needs(needs, FACES, most=3) == [
        NeedPhoto(what=["the gel", "the foam"], photo=4, has_face=False)
    ]


def test_a_need_with_no_photo_the_job_has_sends_nothing() -> None:
    assert photos_for_needs([{"what": "the gel", "photos": [9]}], FACES, most=3) == []


def test_needs_past_the_most_that_fit_are_left_out() -> None:
    needs = [{"what": str(n), "photos": [n]} for n in (3, 4, 5, 6)]
    assert [need.photo for need in photos_for_needs(needs, FACES, most=2)] == [3, 4]
