"""Why work failed, in plain words for the shop owner (agents.loop.why_it_failed_plainly).
Each reason is shown through the chat in the scene tests; this covers one no chat test
reaches."""

from agents.loop import why_it_failed_plainly
from gateway.gateway import UnreadableImage


def test_a_picture_no_model_can_read_says_which_kinds_they_can() -> None:
    assert why_it_failed_plainly(UnreadableImage("photo.heic is a HEIC")) == (
        "a picture was in a kind models can't read: only PNG, JPEG, WebP or GIF."
    )
