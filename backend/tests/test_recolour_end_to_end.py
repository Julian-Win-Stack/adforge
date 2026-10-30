"""One recolour asked for on a library the first message built (spec #210 point 8): the
owner's first message through the chat, the picker, the labelling call, the picture model
and the recolour's check faked at the gateway. Cut-and-send, which will ask for recolours
before each send, isn't built yet, so the test asks for the one recolour itself."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel
from gateway.types import BlockedBySafetyFilter
from jobs.library import Recolour, library_of
from jobs.library_items import library_text
from jobs.models import Job
from jobs.recolour import recolour

from .test_library_build import OWNER_ALONE, first_message
from .test_picker_labels import labelled_page, notices  # noqa: F401 (a fixture)

pytestmark = pytest.mark.django_db(transaction=True)

# The shop owner's photo shows the product in red, alone (so it needs no product-only
# version): the colour reference.
RED_OWNER_PHOTO = {**OWNER_ALONE, "colour": "red", "label": "The red bottle on a shelf."}


def test_a_page_photo_recoloured_to_the_owners_photos_colour_joins_the_library(
    api: APIClient,
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    session_id: str,
    say: Callable[..., None],
) -> None:
    first_message(fake_model, say, labelled_page, RED_OWNER_PHOTO)
    job = Job.objects.get()
    before = notices(api, session_id)

    made = recolour(job, 1, "red", reference=6, scene=1)

    assert made.record == Recolour(of=1, colour="red", outcome="done", made=7)
    assert library_text(job).splitlines()[-1] == (
        "7. The amber bottle from the front, cap on. Kind side, side front, alone yes. "
        "Colour red. From item 1, recoloured red."
    )
    assert notices(api, session_id) == before


def test_a_paint_refused_twice_sends_the_original_and_tells_the_developer_not_the_owner(
    sentry_events: list[dict[str, Any]],
    api: APIClient,
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    session_id: str,
    say: Callable[..., None],
) -> None:
    first_message(fake_model, say, labelled_page, RED_OWNER_PHOTO)
    job = Job.objects.get()
    before = notices(api, session_id)
    fake_model.respond("recolour_picture", *[BlockedBySafetyFilter("no")] * 2)

    made = recolour(job, 1, "red", reference=6, scene=1)

    assert made.record.outcome == "fell_back"
    assert made.picture == library_of(job)[0]
    # The shop owner can do nothing about it: their chat doesn't show it.
    assert notices(api, session_id) == before
    job.refresh_from_db()
    warning = job.warnings[-1]
    assert (warning["kind"], warning["level"]) == ("recolour_failed", "problem")
    assert "Picture 1" in warning["text"] and "in its own colour, amber" in warning["text"]
    [event] = [e for e in sentry_events if e["fingerprint"] == ["notice", "recolour_failed"]]
    assert event["tags"]["scene"] == "1"
    assert event["tags"]["session"] == session_id
    assert len(library_of(job)) == 6
