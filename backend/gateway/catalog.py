"""Which model does each job, and what each model costs. Change a model here, nowhere else."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings

MODEL_FOR_PURPOSE: dict[str, str] = {
    "produce": "gpt-5.6-sol",
    "check_page": "gpt-5-mini",
    # The copy test (docs/scraping-test/copy-test/results.md) kept 87% of the product's facts
    # and let no other product's sentence through at 0.2 cents a page.
    "copy_page_text": "gpt-6-luna",
    # The picker reads a marked screenshot of the page: 92% of its photos were the right
    # product's (docs/scraping-test/ref-photo-test/results.md).
    "pick_photos": "gpt-5.6-sol",
    # A photo the picker never saw gets its Face note from the same model, as the picker
    # gives it for the photos it picks.
    "note_face": "gpt-5.6-sol",
    # A photo the picker never saw gets the picker's fixed fields from the same model, as the
    # picker gives them for the photos it picks (spec #210 point 1).
    "label_photo": "gpt-5.6-sol",
    "plan_ad": "gpt-5.6-sol",
    # The watcher: watches a page video whole, sound and all (spec #210 point 3), as Gemini
    # Flash did in the watcher test (runs/watcher-test/results.md, Oct 27 2026).
    "watch_video": "gemini-3.8-flash",
    # The watcher's one-word probe of a part of a video its safety filter refused (spec #210
    # point 3): the watcher's own model, so its filter is the one probed.
    "probe_video": "gemini-3.8-flash",
    "fact_check": "gpt-5.6-terra",
    "rewrite_line": "gpt-5.6-sol",
    "shorten_script": "gpt-5.6-sol",
    "shorten_line": "gpt-5.6-sol",
    "lengthen_line": "gpt-5.6-sol",
    "draw_person": "gpt-image-2.5-sunburst",
    "choose_starting_picture": "gpt-5.6-sol",
    "choose_broll_picture": "gpt-5.6-sol",
    # A B-roll scene's video prompt written again from a note, by the same model that wrote it.
    "rewrite_clip_prompt": "gpt-5.6-sol",
    # A B-roll clip's first-send prompt (#210 point 16): written by the model that writes
    # B-roll video prompts today.
    "write_first_send_prompt": "gpt-5.6-sol",
    "make_starting_picture": "gpt-image-2.5-sunburst",
    # A clean picture of only the thing a shop owner's photo is taken for (Reference photo,
    # CONTEXT.md), drawn as starting pictures are.
    "make_reference_photo_picture": "gpt-image-2.5-sunburst",
    # The product-only version of a library photo that shows the product with other things
    # (spec #210 point 6): drawn as the clean picture of a shop owner's thing is.
    "make_product_only": "gpt-image-2.5-sunburst",
    # The cut-out check (spec #210 point 6): the product-only version, or the clean picture of
    # a shop owner's thing, beside the photo it was cut from. The picture judges' model.
    "check_cut_out": "gpt-6.1-sol",
    # An owner part's own picture (spec #210 point 20): drawn from its sentence beside a
    # product photo, or changed from the shop owner's photo of it, as cut-outs are drawn; and
    # its check against the sentence, on the picture judges' model.
    "make_part_picture": "gpt-image-2.5-sunburst",
    "check_part_picture": "gpt-6.1-sol",
    # Whether the thing in the shop owner's photo is in a scene's starting picture, belongs in
    # it from the start, or only shows during the clip: the starting picture's judge, as it is
    # proven at seeing what our pictures show (Julian 2026-10-15, D10).
    "route_reference_photo": "gpt-6.1-sol",
    # The ad's before-picture: planned from every B-roll scene, then drawn.
    "plan_before_picture": "gpt-5.6-sol",
    "make_before_picture": "gpt-image-2.5-sunburst",
    # The before-picture's room alone, then the presenter standing in it: drawn once per ad
    # from it and the portrait, as the before-picture is.
    "make_room_alone": "gpt-image-2.5-sunburst",
    # A library picture recoloured to a colour reference's colour (spec #210 point 8), on
    # the picture model that cleans the shop owner's photos, and its check, on the picture
    # judges' model.
    "recolour_picture": "gpt-image-2.5-sunburst",
    "check_recolour": "gpt-6.1-sol",
    "make_person_in_room": "gpt-image-2.5-sunburst",
    # The fixing agent's place_edit (#237), for one scene: a small edit of its room or
    # person-in-room picture, or a new room drawn fresh and the presenter standing in it.
    "edit_scene_place": "gpt-image-2.5-sunburst",
    "draw_scene_room": "gpt-image-2.5-sunburst",
    "make_scene_person_in_room": "gpt-image-2.5-sunburst",
    # Judges the starting picture beside its shop photo. With the photo, sol caught 7 of Julian's
    # 10 wrong pictures and failed none of 88 good ones; luna missed the bent neck, the bag clasp
    # and the logo (evals/picture-judge/wrong-pictures-and-photo-test.md; Julian 10 Oct 23:05).
    "check_starting_picture": "gpt-6.1-sol",
    # Asks the complaint back about a fix's new starting picture, beside the old one (#131):
    # the starting picture's judge, as Julian chose it for picture judges.
    "check_fix": "gpt-6.1-sol",
    # The decider (agents/decider.py): reads the shop owner's notes once the ad has a clip and
    # looks at pictures and clip frames before it plans each scene's fix. The picture judges'
    # model, as it is proven at seeing what our pictures show (fix redesign, ticket 05).
    "decide_fixes": "gpt-6.1-sol",
    # The fixing agent (agents/fixing_agent.py): owns one failed B-roll scene, films it again
    # and reads the judge's answer, until a try passes or it gives up. The decider's model.
    "fix_scene": "gpt-6.1-sol",
    # The B-roll clip judge's two checks (jobs/clip_judge.py). Tested on sol; the model check
    # of 2026-10-13 found luna matches it, at the edge, so stage 4 may move both to luna
    # (Claude Doc "Clip judge discussion", Results).
    "check_clip_sides": "gpt-6.1-sol",
    "check_clip_motion": "gpt-6.1-sol",
    # A clip with its sent list (#228): PRODUCT replaces SIDES, on the model the judge build
    # brief tested it on, and the first question asks whether the references show enough.
    "check_clip_product": "gpt-6.1-sol",
    "check_clip_references": "gpt-6.1-sol",
    "design_voice": "inworld-tts-2",
    "measure_voice": "inworld-tts-2",
    "speak_line": "inworld-tts-2",
    "transcribe_line": "scribe_v2",
    # A clip is asked for, then waited for and fetched: two calls, only the first paid. Each
    # kind has its own model: HeyGen's talking clips passed the blind-graded eval (#87) 8 of
    # 8; B-roll is Boreal-H3, on Creatify's own API (docs/broll-picture-logic.md, item 31).
    "make_talking_clip": "heygen/avatar-iv",
    "collect_talking_clip": "heygen/avatar-iv",
    "make_broll_clip": "creatify/boreal-h3",
    "collect_broll_clip": "creatify/boreal-h3",
    "make_music": "sonilo/v1.1/text-to-music",
}

# How long a model may take to answer, in seconds. A slow purpose has its own time, and once it
# has taken that long it isn't asked again (gateway.types.AnswerTookTooLong): the clip judge
# looks at up to 50 pictures and reasons over them (the harness waited up to 600), and the
# clip step waits on it.
ANSWER_SECONDS = 120
SLOW_ANSWER_SECONDS: dict[str, float] = {
    "check_clip_sides": 600,
    "check_clip_motion": 600,
    "check_clip_product": 600,
    "check_clip_references": 600,
    # A whole video, watched with thinking: the watcher test's calls took 31 to 55 s.
    "watch_video": 600,
    "probe_video": 600,
}

# How hard a Gemini model thinks before it answers, for a purpose that isn't to think as
# settings.GEMINI_THINKING_LEVEL says: the watcher's probe asks only whether the safety filter
# lets a part of a video through, in one word, and thinking is billed as output.
THINKING_LEVEL_FOR_PURPOSE: dict[str, str] = {
    "probe_video": "MINIMAL",
}


@dataclass(frozen=True)
class ContextWindow:
    """How many tokens a model takes in one call: input and output together (`context`), and
    of them at most `max_input` in."""

    context: int
    max_input: int


# The context window of each model an agent takes turns on, beside its prices: from the
# model's page (gpt-5.6-sol: developers.openai.com/api/docs/models/gpt-5.6-sol, 2026-10-29).
# The producer's conversation folds past a share of it (settings.FOLD_PAST_SHARE_OF_WINDOW).
CONTEXT_WINDOW: dict[str, ContextWindow] = {
    "gpt-5.6-sol": ContextWindow(context=1_050_000, max_input=922_000),
}


def max_input_tokens(model: str) -> int:
    """The most input tokens one call of `model` takes: its own window's, or our Azure
    deployment's limit when that is set and lower (settings.AZURE_OPENAI_MAX_INPUT_TOKENS)."""
    window = CONTEXT_WINDOW[model].max_input
    azure: int = settings.AZURE_OPENAI_MAX_INPUT_TOKENS
    return min(window, azure) if azure else window


# US dollars per million tokens: (input, output). From OpenAI's pricing page, and Gemini's
# from Google's (ai.google.dev/gemini-api/docs/pricing, 2026-10-29), whose output price
# includes thinking. Gemini 3.8 Flash's standard paid price holds through December 31, 2026,
# and doubles to $1.50 and $7.50 on January 1, 2027.
PRICE_PER_MILLION_TOKENS: dict[str, tuple[Decimal, Decimal]] = {
    "gpt-5-mini": (Decimal("0.25"), Decimal("2.00")),
    "gpt-6-luna": (Decimal("0.10"), Decimal("0.50")),
    "gpt-6.1-sol": (Decimal("2.00"), Decimal("10.00")),
    "gpt-5.6-sol": (Decimal("4.00"), Decimal("20.00")),
    "gpt-5.6-terra": (Decimal("2.00"), Decimal("12.00")),
    "gemini-3.8-flash": (Decimal("0.75"), Decimal("3.75")),
}

# US dollars per million tokens for picture models: (text input, picture input, picture
# output). From OpenAI's pricing page. Pictures go in when one is made from others, such as
# a scene's starting picture from the portrait and a product photo.
PRICE_PER_MILLION_PICTURE_TOKENS: dict[str, tuple[Decimal, Decimal, Decimal]] = {
    "gpt-image-2.5-sunburst": (Decimal("5.00"), Decimal("8.00"), Decimal("30.00")),
}

# US dollars per million characters spoken. From Inworld's pay-as-you-go pricing. Designing a
# voice is charged at the same rate for the sample it speaks.
PRICE_PER_MILLION_CHARACTERS: dict[str, Decimal] = {
    "inworld-tts-2": Decimal("25.00"),
}

# US dollars per hour of audio heard. From ElevenLabs' API pricing page (Scribe, not realtime).
PRICE_PER_HOUR_OF_AUDIO: dict[str, Decimal] = {
    "scribe_v2": Decimal("0.22"),
}

# US dollars per second of video made. Boreal-H3's from Creatify on 2026-10-03: 0.4 credits a
# second at the 768p the adapter asks for, at $99 for 500 credits (API Starter); up to 5
# example pictures are free; each second of reference video sent costs 0.4 credits too
# (spec #210, Further Notes), so it is billed at the same rate. HeyGen's from its own
# pricing page on 2026-09-29: a photo avatar is 0.1 credits a second at $0.50 a credit, the
# same at 720p and 1080p.
PRICE_PER_SECOND_OF_VIDEO: dict[str, Decimal] = {
    "creatify/boreal-h3": Decimal("0.0792"),
    "heygen/avatar-iv": Decimal("0.05"),
}


# US dollars per second of music made. From fal's model page on 2026-09-17
# (docs/video-model-tests.md).
PRICE_PER_SECOND_OF_MUSIC: dict[str, Decimal] = {
    "sonilo/v1.1/text-to-music": Decimal("0.0025"),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    input_price, output_price = PRICE_PER_MILLION_TOKENS[model]
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


def picture_cost_usd(
    model: str, input_tokens: int, output_tokens: int, picture_input_tokens: int = 0
) -> Decimal:
    """What a picture cost. `picture_input_tokens` are the part of `input_tokens` that were
    pictures."""
    text_price, picture_in_price, output_price = PRICE_PER_MILLION_PICTURE_TOKENS[model]
    text_tokens = input_tokens - picture_input_tokens
    return (
        text_tokens * text_price
        + picture_input_tokens * picture_in_price
        + output_tokens * output_price
    ) / 1_000_000


def speech_cost_usd(model: str, characters: int) -> Decimal:
    return characters * PRICE_PER_MILLION_CHARACTERS[model] / 1_000_000


def transcription_cost_usd(model: str, seconds: float) -> Decimal:
    """What transcribing `seconds` of audio costs, to the millionth of a dollar a bill is
    recorded to: an hour's price divided into seconds never ends."""
    cost = Decimal(str(seconds)) * PRICE_PER_HOUR_OF_AUDIO[model] / 3_600
    return cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def video_cost_usd(model: str, seconds: float) -> Decimal:
    return Decimal(str(seconds)) * PRICE_PER_SECOND_OF_VIDEO[model]


def music_cost_usd(model: str, seconds: int) -> Decimal:
    return seconds * PRICE_PER_SECOND_OF_MUSIC[model]
