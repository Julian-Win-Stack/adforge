"""Card-size copies of the pictures a clip was sent, for the scene's card in the chat. Made
when the clip's sent list is kept, once per picture file, so the chat only links to them."""

import io
import logging
from functools import partial

import PIL.Image

from adforge import file_store
from chat.models import Message

from . import notices
from .models import ProducedItem
from .notices import post_notice

logger = logging.getLogger(__name__)

# The longest side of a picture as a scene's card shows it, twice the card's own size so it
# stays sharp on a phone's screen.
CARD_PICTURE_SIDE = 480


def card_key(file: str) -> str:
    """Where the card-size copy of the picture kept at `file` is kept."""
    return f"cards/{file}.jpg"


def card_url(file: str) -> str:
    """The link to the card-size copy of the picture at `file`; the picture itself when no
    copy could be made, which its clip's notice said."""
    key = card_key(file)
    return file_store.url(key if file_store.exists(key) else file)


def _card_picture(file: str) -> bytes:
    picture = PIL.Image.open(io.BytesIO(file_store.read(file)))
    picture.thumbnail((CARD_PICTURE_SIDE, CARD_PICTURE_SIDE))
    out = io.BytesIO()
    picture.convert("RGB").save(out, format="JPEG", quality=85)
    return out.getvalue()


def make_cards(clip: ProducedItem, named: dict[str, list[str]]) -> None:
    """Keep a card-size copy of each picture file in `named` (file -> what the clip calls it,
    such as "Image 3"). One that can't be made is logged and told in a red notice: the card
    shows that picture full size, and the clip and its sent list stand."""
    failed: list[str] = []
    for file, names in named.items():
        try:
            file_store.save_once(
                card_key(file), partial(_card_picture, file), whole=file_store.whole_picture
            )
        except Exception:
            logger.exception("No card-size copy of %s (%s) for clip %s", file, names, clip.pk)
            failed.extend(names)
    if failed:
        post_notice(
            clip.job,
            notices.CARD_PICTURE,
            _no_cards(clip, failed),
            Message.Level.PROBLEM,
            scene=clip.scene.number if clip.scene is not None else None,
        )


def _no_cards(clip: ProducedItem, failed: list[str]) -> str:
    listed = failed[0] if len(failed) == 1 else f"{', '.join(failed[:-1])} and {failed[-1]}"
    one = len(failed) == 1
    whose = f"Scene {clip.scene.number}'s clip" if clip.scene is not None else "A clip"
    return (
        f"{whose} was sent {listed}, but {'it' if one else 'they'} couldn't be made small for "
        f"the scene's card, so the card shows {'it' if one else 'them'} full size. The clip "
        "itself is not affected."
    )
