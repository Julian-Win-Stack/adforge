"""One recolour asked for on a library the first message built (spec #210 point 8): the
owner's first message through the chat, the picker, the labelling call, the picture model
and the recolour's check faked at the gateway. Cut-and-send, which will ask for recolours
before each send, isn't built yet, so the test asks for the one recolour itself."""

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel
from jobs.library import Recolour, library_of
from jobs.library_items import library_text
from jobs.models import Job
from jobs.recolour import recolour

from .test_library_build import OWNER_LABEL, first_message
from .test_picker_labels import labelled_page, notices  # noqa: F401 (a fixture)
from .test_recolour import RECOLOUR_BAD, RECOLOUR_OK

pytestmark = pytest.mark.django_db(transaction=True)

# The shop owner's photo shows the product in red: the colour reference.
RED_OWNER_PHOTO = {**OWNER_LABEL, "colour": "red", "label": "The red bottle on a shelf."}


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
    fake_model.respond("check_recolour", RECOLOUR_BAD, RECOLOUR_OK)

    made = recolour(job, 1, "red", reference=6)

    assert made.record == Recolour(of=1, colour="red", outcome="done", made=7)
    assert library_text(job).splitlines()[-1] == (
        "7. The amber bottle from the front, cap on. Kind side, side front, alone yes. "
        "Colour red. From item 1, recoloured red."
    )
    assert notices(api, session_id) == before


def test_a_recolour_that_keeps_failing_sends_the_original_and_says_so_in_red(
    api: APIClient,
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    session_id: str,
    say: Callable[..., None],
) -> None:
    first_message(fake_model, say, labelled_page, RED_OWNER_PHOTO)
    job = Job.objects.get()
    fake_model.respond("check_recolour", RECOLOUR_BAD, RECOLOUR_BAD, RECOLOUR_BAD)

    made = recolour(job, 1, "red", reference=6)

    assert made.record.outcome == "fell_back"
    assert made.picture == library_of(job)[0]
    level, text = notices(api, session_id)[-1]
    assert level == "problem"
    assert "picture 1" in text and "in its own colour, amber" in text
    assert len(library_of(job)) == 6
