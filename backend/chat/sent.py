"""What a clip in the chat was sent (jobs.library.SentList), as the scene's card shows it:
each picture as a link to a copy small enough for a card (jobs.cards), each piece as a link to
its file."""

from typing import Any

from adforge import file_store
from jobs.cards import card_url
from jobs.library import SentList, sent_list_of
from jobs.models import ProducedItem

# A clip made before its sent list was kept shows this: nothing sent that the chat knows of.
NOTHING_SENT: dict[str, Any] = {
    "colour": "",
    "pictures": [],
    "pieces": [],
    "left_out": [],
    "recolours": [],
    "prompt": "",
}


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
            each["url"] = card_url(each["file"])
    for piece in data["pieces"]:
        piece["url"] = file_store.url(piece["file"])
    return data
