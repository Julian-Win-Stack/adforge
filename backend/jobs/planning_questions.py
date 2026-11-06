"""The approved outcomes when an item in the planner's question is unanswered."""

from typing import Literal

QuestionKind = Literal[
    "photos",
    "size",
    "price",
    "stock",
    "in_use_photo",
    "before_after_photo",
    "part_photo",
    "demo",
    "page_conflict",
]

# Stock has no approved fallback. Keep it waiting, even when the other items expire.
DEFAULTS: dict[QuestionKind, str | None] = {
    "photos": "I'll plan with the photos we have.",
    "size": (
        "I'll pick the size or pack that looks like the main one on your page, and say no price."
    ),
    "price": "I'll leave the price out.",
    "stock": None,
    "page_conflict": None,
    "in_use_photo": "I'll go ahead without a photo of it in use.",
    "before_after_photo": "I'll go ahead without a before-and-after photo.",
    "part_photo": "I'll go ahead without that photo of the part.",
    "demo": "I won't film that use; the person will say the line to camera.",
}


def default_for(kind: QuestionKind) -> str | None:
    """The single approved default for this kind; None means the owner must answer."""
    return DEFAULTS[kind]
