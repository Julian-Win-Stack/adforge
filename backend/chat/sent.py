"""What a clip in the chat was sent (jobs.library.SentList), as the scene's card shows it:
each picture as a link to a copy small enough for a card, each piece as a link to its file."""

import io
from functools import partial
from typing import Any

import PIL.Image

from adforge import file_store
from jobs.library import SentList, sent_list_of
from jobs.models import ProducedItem

# The longest side of a picture as a scene's card shows it, twice the card's own size so it
# stays sharp on a phone's screen.
CARD_PICTURE_SIDE = 480

# A clip made before its sent list was kept shows this: nothing sent that the chat knows of.
NOTHING_SENT: dict[str, Any] = {
    "colour": "",
    "pictures": [],
    "pieces": [],
    "left_out": [],
    "recolours": [],
    "prompt": "",
}


def card_key(file: str) -> str:
    """Where the card-size copy of the picture kept at `file` is kept."""
    return f"cards/{file}.jpg"


def _card_picture(file: str) -> bytes:
    picture = PIL.Image.open(io.BytesIO(file_store.read(file)))
    picture.thumbnail((CARD_PICTURE_SIDE, CARD_PICTURE_SIDE))
    out = io.BytesIO()
    picture.convert("RGB").save(out, format="JPEG", quality=85)
    return out.getvalue()


def _whole_picture(data: bytes) -> bool:
    try:
        PIL.Image.open(io.BytesIO(data)).verify()
    except OSError, SyntaxError:
        return False
    return True


def make_cards(clip: ProducedItem) -> None:
    """Keep a card-size copy of every picture `clip` was sent or left out, once each."""
    sent = sent_list_of(clip)
    if sent is None:
        return
    for file in {each.file for each in [*sent.pictures, *sent.left_out]}:
        file_store.save_once(card_key(file), partial(_card_picture, file), whole=_whole_picture)


def shown(clip: ProducedItem | None) -> dict[str, Any]:
    """What `clip` was sent, as the fixture's JSON, with a `url` beside each file: a card-size
    copy for each picture and the piece's own file for each piece. The empty sent list for a
    clip made before its sent list was kept, or one the chat doesn't know."""
    sent: SentList | None = sent_list_of(clip) if clip is not None else None
    if sent is None:
        return dict(NOTHING_SENT)
    data: dict[str, Any] = sent.model_dump(mode="json", by_alias=True)
    for part in ("pictures", "left_out"):
        for each in data[part]:
            each["url"] = file_store.url(card_key(each["file"]))
    for piece in data["pieces"]:
        piece["url"] = file_store.url(piece["file"])
    return data
