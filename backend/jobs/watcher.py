"""The watcher (spec #210 points 3 to 5): every page video with a file is watched once, all at
once, as the page is read and before the ad is planned, by a model that sees and hears the
whole video. It says whether the video shows the product the page sells, and writes its
moments (one action each, at most the video API's longest piece), one look frame per product
state it shows, the product's colour in it and what is said in it.

The moments are kept on the video (jobs.library.VideoWatch). Each look frame is pulled from
the file with ffmpeg and enters the library with the photo's fields, its video and its
second. Until the planner reads the library, it gets what it got from the old video reader:
each video's transcript and its look frames as video pictures. A video that doesn't show this
product is thrown out, and one over WATCH_VIDEO_MOST_SECONDS isn't watched; the owner is told
of each in grey. A video that can't be watched is told in red, and the ad is planned without
it.

A video the model's safety filter refuses (requirement 22) is asked again as it is, then
without its sound; then it is halved in time, each half probed with a cheap one-word call,
the halves that pass kept and the ones that fail halved again, down to LEAST_PART_SECONDS and
at most MOST_PROBES probes. Each part kept is watched, told which seconds of the video it
is, and what was seen in each is merged in the video's own seconds. Every refusal is told in
red, with what was dropped and why, and kept with the watch."""

import contextvars
import logging
from collections import deque
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Annotated, Self

from django.conf import settings
from django.db import transaction
from pydantic import BaseModel, ConfigDict, Field, model_validator

from adforge import file_store
from chat.models import Message
from gateway.gateway import MOST_REFUSED_PROBES, watch_video
from gateway.types import BlockedBySafetyFilter, Handoff, UnusableReply
from jobs import notices, page
from jobs.library import (
    VIDEO_API_MOST_PIECE_SECONDS,
    Colour,
    FilterRefusal,
    LookFrame,
    PictureLabel,
    Span,
    VideoWatch,
    WatchedMoment,
    add_to_library,
    keep_watch,
    number_moments,
)
from jobs.models import Job, ProductVideo
from jobs.notices import post_notice
from jobs.product_videos import cut_part, frame_second, frames_of
from jobs.threads import closing_its_connection
from jobs.work import save_video_pictures

logger = logging.getLogger(__name__)

PURPOSE = "watch_video"
# A video's transcript when nothing is said in it, as the planner has always been told.
MUSIC_ONLY = "music only"
# An answer the schema refuses is asked for again this many times.
ASKED_AGAIN = 1
# A video the safety filter refuses is halved at whole multiples of this many seconds from its
# start, so a refused part of this length isn't halved again but dropped.
LEAST_PART_SECONDS = 5.0
# The most one-word probes the search for a refused video's bad seconds makes; the parts not
# probed by then are dropped.
MOST_PROBES = MOST_REFUSED_PROBES
PROBE_PURPOSE = "probe_video"
# The watcher looks at one frame a second and answers in whole seconds, so an end or a look
# frame up to this far past the video's end is its last second, and is taken as the end.
PAST_THE_END_SECONDS = 1.0

LONGEST_MOMENT = (
    f"A moment lasts at most {VIDEO_API_MOST_PIECE_SECONDS} seconds, the longest piece of "
    "video the video model takes: split a longer action at a natural point, such as where "
    "one step ends and the next begins."
)
INSTRUCTIONS = f"""You watch one video from a product's shop page, with its sound. You are \
given the page's link and its text about the product, and the video's length in seconds. Give \
every time in seconds from the start of the video, never past its end.

First say whether the video shows the product the page sells. When it shows another product, \
or no product, say so; the rest of your answer is then not used.

Moments: every moment where the product is shown doing something or being used, in time \
order. Each moment is one action: the second it starts, the second it ends, what happens in \
product terms, and what is on screen (the product's state, the hands, the output). \
{LONGEST_MOMENT}

Look frames: for each state the product is shown in (such as closed, open, in use, or its \
result), the one second where that state is seen best, with that frame's fields: its kind, \
the side of the product it shows, whether the product is alone on a plain background, the \
product's colour in it, the product's place in a line-up when it is one, and one sentence \
saying what the frame shows, as a caption would.

Colour: the product's colour in the video, in one word.

Transcript: what is said in the video, word for word; empty when nothing is said.

When you are told the video is a part of a longer one, you see and hear only that part, from \
its start second to its end second in the longer one: give every time from the start of the \
part you are given. When you are told its sound was taken out, leave the transcript empty."""

PROBE_INSTRUCTIONS = """You are shown a part of a video. Say in one word what is on screen \
most of the time."""


class VideoPart(Handoff):
    """Which seconds of a longer video the video sent is: from `start` to `end` of a video
    `of_seconds` long."""

    start: float
    end: float
    of_seconds: float


class WatcherHandoff(Handoff):
    """What the watcher is handed beside the video: the page and the video's length."""

    product_url: str
    page_text: str
    seconds: float
    part: VideoPart | None = Field(
        description="Which seconds of the page's video this is, when it is only a part of it."
    )
    sound: bool = Field(description="Whether the video is sent with its sound.")


class ProbeHandoff(Handoff):
    """What the one-word probe is handed beside the part of a video: its length and which
    seconds of the page's video it is."""

    seconds: float
    part: VideoPart


class ProbeAnswer(BaseModel):
    """The probe's one word: whether the model answers at all is all it is asked for."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    word: str = Field(description="One word.")


class SeenMoment(WatchedMoment):
    """A moment as the watcher must write it: at most the video API's longest piece."""

    @model_validator(mode="after")
    def _at_most_the_longest_piece(self) -> Self:
        if self.end - self.start > VIDEO_API_MOST_PIECE_SECONDS:
            raise ValueError(
                f"a moment lasts at most {VIDEO_API_MOST_PIECE_SECONDS} s; split a longer one"
            )
        return self


class SeenLookFrame(PictureLabel):
    """A look frame: the second it is at, and the fields its picture enters the library
    with."""

    second: Annotated[float, Field(ge=0, description="The second the frame is at.")]


class WatcherAnswer(BaseModel):
    """What the watcher writes for one video."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    shows_this_product: bool = Field(
        description="Whether the video shows the product the page sells."
    )
    moments: list[SeenMoment]
    look_frames: list[SeenLookFrame]
    colour: Colour = Field(description="The product's colour in the video, one word.")
    transcript: str = Field(description="What is said in the video; empty when nothing is.")


def _answer_for(seconds: float) -> type[WatcherAnswer]:
    """The watcher's answer for a video `seconds` long: no moment starting at or past its end,
    and nothing more than PAST_THE_END_SECONDS past it."""

    class WatcherAnswerForThisVideo(WatcherAnswer):
        @model_validator(mode="after")
        def _inside_the_video(self) -> Self:
            ends = [moment.end for moment in self.moments]
            ends += [frame.second for frame in self.look_frames]
            starts_past = any(moment.start >= seconds for moment in self.moments)
            if starts_past or any(end > seconds + PAST_THE_END_SECONDS for end in ends):
                raise ValueError(f"the video is {seconds:g} s long; nothing is past its end")
            return self

    WatcherAnswerForThisVideo.__name__ = WatcherAnswer.__name__
    return WatcherAnswerForThisVideo


def watch_videos(
    job: Job, page_text: str, *, only: Sequence[ProductVideo] | None = None
) -> list[str]:
    """Watch each of the job's videos with a file that wasn't watched yet, all at once, and
    keep what was seen; `only` these of them, when given. Gives back a line for the producer
    for each video not watched or that couldn't be."""
    chosen = None if only is None else {video.pk for video in only}
    waiting = [
        video
        for video in job.videos.exclude(file="").order_by("position")
        if video.watched is None and not video.thrown_out and (chosen is None or video.pk in chosen)
    ]
    told: list[str] = []
    to_watch: list[ProductVideo] = []
    for video in waiting:
        if (video.duration or 0) > settings.WATCH_VIDEO_MOST_SECONDS:
            told.append(_too_long(job, video))
        else:
            to_watch.append(video)
    with ThreadPoolExecutor(max_workers=max(1, len(to_watch))) as pool:
        asked = [
            pool.submit(
                contextvars.copy_context().run,
                closing_its_connection,
                _watch,
                job,
                video,
                page_text,
            )
            for video in to_watch
        ]
        # Kept in the videos' order, so the library numbers its look frames the same way
        # however the answers came.
        for video, each in zip(to_watch, asked, strict=True):
            try:
                watched = each.result()
                if watched.refused is not None:
                    told.append(_refused(job, video, watched.refused))
                if watched.seen is None:
                    video.thrown_out = _refused_all(watched.refused)
                    video.save(update_fields=["thrown_out"])
                else:
                    told += _keep(job, video, watched.seen, watched.refused)
            except Exception as error:
                # Whatever stops one video's watch, the page is read and the ad planned
                # without it: never quietly.
                logger.exception("Product video %s couldn't be watched", video.pk)
                post_notice(
                    job,
                    notices.PAGE_VIDEO,
                    f"Your page's video {video.position} couldn't be watched, so the ad is "
                    "planned without it.",
                    Message.Level.PROBLEM,
                )
                told.append(
                    f"The product's video {video.position} couldn't be watched ({error}), so "
                    "the ad is planned without it."
                )
    return told


@dataclass(frozen=True)
class _Watched:
    """What the watcher saw in a video, or None when its safety filter refused all of it,
    and the record of any refusal."""

    seen: WatcherAnswer | None
    refused: FilterRefusal | None


def _watch(job: Job, video: ProductVideo, page_text: str) -> _Watched:
    """What the watcher saw in `video`, past its safety filter as requirement 22 says: asked
    again as it is, then without its sound, then in the parts of it the filter lets through."""
    seconds = float(video.duration or 0)
    for_model = page.for_model(page_text)

    def ask(key: str, part: Span | None = None, *, sound: bool = True) -> WatcherAnswer:
        sent = seconds if part is None else part.end - part.start
        return _asked(
            job,
            key,
            WatcherHandoff(
                product_url=job.product_url,
                page_text=for_model,
                seconds=sent,
                part=None if part is None else _part(part, seconds),
                sound=sound,
            ),
        )

    for refusal in [None, FilterRefusal(reason="asked again", dropped=[], probes=0)]:
        try:
            return _Watched(ask(video.file), refusal)
        except BlockedBySafetyFilter:
            logger.warning("The safety filter refused product video %s", video.pk)
    try:
        silent = ask(cut_part(video.file, 0, seconds, sound=False), sound=False)
        return _Watched(
            silent.model_copy(update={"transcript": ""}),
            FilterRefusal(reason="sound", dropped=[], probes=0),
        )
    except BlockedBySafetyFilter:
        logger.warning("The safety filter refused product video %s without sound", video.pk)
    passed, dropped, probes = _past_the_filter(job, video, seconds)
    parts: list[tuple[Span, WatcherAnswer]] = []
    for part in passed:
        try:
            parts.append((part, ask(cut_part(video.file, part.start, part.end, sound=True), part)))
        except BlockedBySafetyFilter:
            # Probed, but refused when watched: dropped as a refused probe's part is.
            dropped.append(part)
    reason = "pictures" if parts else "all"
    refused = FilterRefusal(reason=reason, dropped=_joined(dropped), probes=probes)
    return _Watched(_merged(parts, seconds) if parts else None, refused)


def _asked(job: Job, key: str, handoff: WatcherHandoff) -> WatcherAnswer:
    """The watcher's answer for the video kept as `key`, asked again ASKED_AGAIN times when
    its answer is refused by the schema."""
    for asked in range(ASKED_AGAIN + 1):
        try:
            return watch_video(
                job=job,
                purpose=PURPOSE,
                instructions=INSTRUCTIONS,
                handoff=handoff,
                video_key=key,
                output=_answer_for(handoff.seconds),
            )
        except UnusableReply:
            if asked == ASKED_AGAIN:
                raise
    raise AssertionError("unreachable")


def _part(part: Span, seconds: float) -> VideoPart:
    return VideoPart(start=part.start, end=part.end, of_seconds=seconds)


def _past_the_filter(
    job: Job, video: ProductVideo, seconds: float
) -> tuple[list[Span], list[Span], int]:
    """The parts of `video`, refused whole, that the safety filter lets through, in time
    order, the parts dropped and how many probes it took: each refused part is halved and its
    halves probed, until a refused part can't be halved or MOST_PROBES are made."""
    passed: list[Span] = []
    dropped: list[Span] = []
    to_probe: deque[Span] = deque()

    def refused(part: Span) -> None:
        halves = _halves(part)
        if halves is None:
            dropped.append(part)
        else:
            to_probe.extend(halves)

    refused(Span(start=0, end=seconds))
    probes = 0
    while to_probe:
        part = to_probe.popleft()
        if probes == MOST_PROBES:
            dropped.append(part)
            continue
        probes += 1
        if _passes(job, video, part, seconds):
            passed.append(part)
        else:
            refused(part)
    return sorted(passed, key=lambda part: part.start), dropped, probes


def _halves(part: Span) -> tuple[Span, Span] | None:
    """`part` cut in two at the whole multiple of LEAST_PART_SECONDS nearest its middle (the
    later one when two are as near), each half at least half that long, or else at its
    middle; None for a part no longer than LEAST_PART_SECONDS, which isn't halved again."""
    least = LEAST_PART_SECONDS
    cuts = [
        least * step
        for step in range(int(part.start // least) + 1, int(part.end // least) + 1)
        if least * step - part.start >= least / 2 and part.end - least * step >= least / 2
    ]
    middle = (part.start + part.end) / 2
    if not cuts:
        if part.end - part.start <= least:
            return None
        cuts = [middle]
    cut = min(cuts, key=lambda at: (abs(at - middle), -at))
    return Span(start=part.start, end=cut), Span(start=cut, end=part.end)


def _passes(job: Job, video: ProductVideo, part: Span, seconds: float) -> bool:
    """Whether the safety filter lets `part` of `video` through, asked with a one-word probe.
    A timeout or an outside service down is never a refusal: it goes up as it is."""
    try:
        watch_video(
            job=job,
            purpose=PROBE_PURPOSE,
            instructions=PROBE_INSTRUCTIONS,
            handoff=ProbeHandoff(seconds=part.end - part.start, part=_part(part, seconds)),
            video_key=cut_part(video.file, part.start, part.end, sound=True),
            output=ProbeAnswer,
        )
    except BlockedBySafetyFilter:
        return False
    except UnusableReply:
        # An answer that couldn't be read was still an answer: the filter let it through.
        return True
    return True


def _joined(spans: Sequence[Span]) -> list[Span]:
    """`spans` in time order, those that touch joined into one."""
    joined: list[Span] = []
    for span in sorted(spans, key=lambda span: span.start):
        if joined and span.start <= joined[-1].end:
            joined[-1] = Span(start=joined[-1].start, end=max(joined[-1].end, span.end))
        else:
            joined.append(span)
    return joined


def _merged(parts: Sequence[tuple[Span, WatcherAnswer]], seconds: float) -> WatcherAnswer:
    """What the watcher saw in the parts of a video `seconds` long, in time order, as one
    answer in the video's own seconds: its moments and look frames, the colour seen for the
    most seconds and what is said, joined. Only the parts that show this product count, when
    one does. A look frame at or past its part's end is dropped, unless the part ends the
    video: the seconds after it are another part's, or were refused, and weren't seen in it."""
    showing = [(part, seen) for part, seen in parts if seen.shows_this_product] or list(parts)
    colours: dict[str, float] = {}
    for part, seen in showing:
        colours[seen.colour] = colours.get(seen.colour, 0.0) + part.end - part.start
    return WatcherAnswer(
        shows_this_product=showing[0][1].shows_this_product,
        moments=[
            SeenMoment.model_validate(
                {
                    **moment.model_dump(),
                    "start": moment.start + part.start,
                    # The watcher may answer up to PAST_THE_END_SECONDS past a part's end: a
                    # moment cut by the part's edge ends at it.
                    "end": min(moment.end, part.end - part.start) + part.start,
                }
            )
            for part, seen in showing
            for moment in seen.moments
        ],
        look_frames=[
            SeenLookFrame.model_validate(
                {
                    **frame.model_dump(),
                    "second": min(frame.second, part.end - part.start) + part.start,
                }
            )
            for part, seen in showing
            for frame in seen.look_frames
            if frame.second < part.end - part.start or part.end >= seconds
        ],
        colour=max(colours, key=lambda colour: colours[colour]),
        transcript=" ".join(
            seen.transcript.strip() for _, seen in showing if seen.transcript.strip()
        ),
    )


def _refused_all(refused: FilterRefusal | None) -> str:
    """Why a video the safety filter refused all of is thrown out: its record, as the watch it
    has none of would have kept it."""
    assert refused is not None, "a video is refused all over only by the safety filter"
    dropped = " and ".join(f"{span.start:g}-{span.end:g}" for span in refused.dropped)
    return (
        f"The AI model's safety filter refused all of it: seconds {dropped} dropped, after "
        f"{refused.probes} probes."
    )


def _refused(job: Job, video: ProductVideo, refused: FilterRefusal) -> str:
    """Tell the owner in red, and the producer, that the safety filter refused `video`: why
    and what was dropped. Gives the producer's line."""
    seconds = float(video.duration or 0)
    dropped = " and ".join(f"{span.start:g}-{span.end:g}" for span in refused.dropped)
    head = "The AI model's safety filter refused"
    match refused.reason:
        case "asked again":
            text = (
                f"{head} your page's video {video.position} once, then let it through when "
                "it was asked again, so nothing of it is dropped."
            )
        case "sound":
            text = (
                f"{head} your page's video {video.position} for its sound (reason: sound), "
                f"so it was watched without it: its sound, seconds 0-{seconds:g}, is dropped "
                "and nothing said in it is kept."
            )
        case "pictures" if not dropped:
            text = (
                f"{head} your page's video {video.position} whole (reason: pictures), then let "
                "each part of it through, so nothing of it is dropped."
            )
        case "pictures":
            text = (
                f"{head} some of your page's video {video.position}'s pictures (reason: "
                f"pictures), so seconds {dropped} of it are dropped and the rest was watched."
            )
        case "all":
            text = (
                f"{head} all of your page's video {video.position} (reason: all), so seconds "
                f"{dropped} of it are dropped and the ad is planned without it."
            )
    post_notice(job, notices.PAGE_VIDEO, text, Message.Level.PROBLEM)
    return (
        f"The product's video {video.position} was refused by the safety filter (reason: "
        f"{refused.reason}; seconds dropped: {dropped or 'none'}; {refused.probes} probes)."
    )


def _keep(
    job: Job, video: ProductVideo, answer: WatcherAnswer, refused: FilterRefusal | None
) -> list[str]:
    """Keep what the watcher saw in `video`: its moments and transcript on it, its look frames
    in the library and as video pictures. A video that doesn't show this product is thrown
    out instead. Gives back what the producer is told."""
    if not answer.shows_this_product:
        video.thrown_out = "The watcher said it doesn't show the product the page sells."
        video.save(update_fields=["thrown_out"])
        post_notice(
            job,
            notices.PAGE_VIDEO,
            f"Your page's video {video.position} doesn't show this product, so it is left out "
            "of the ad.",
            Message.Level.INFO,
        )
        return [f"The product's video {video.position} doesn't show this product: left out."]
    seconds = float(video.duration or 0)
    # Every frame is pulled before anything is kept, so a video whose frames can't be pulled
    # keeps nothing, and is watched again on the next read of the page.
    pulled_at = [frame_second(video, frame.second) for frame in answer.look_frames]
    pictures = frames_of(video, pulled_at)
    with transaction.atomic():
        pulled = []
        for number, (frame, second, picture) in enumerate(
            zip(answer.look_frames, pulled_at, pictures, strict=True), 1
        ):
            key = file_store.save(
                f"jobs/{job.pk}/videos/{video.position}-look-{number}.jpg", picture
            )
            label = PictureLabel.model_validate(frame.model_dump(exclude={"second"}))
            add_to_library(job, key, label, LookFrame(video=video.position, second=second))
            pulled.append({"key": key, "second": second, "shows": frame.label})
        # The same look frames as video pictures, the product photos the starting picture and
        # the fact check are shown.
        save_video_pictures(job, video, pulled, pulled)
        # Unheard is not nothing said: a video watched without its sound has no transcript.
        unheard = refused is not None and refused.reason == "sound"
        video.transcript = answer.transcript.strip() or ("" if unheard else MUSIC_ONLY)
        video.save(update_fields=["transcript"])
        # Last: a video is watched once all it gave is kept.
        keep_watch(
            video,
            VideoWatch(
                moments=number_moments(
                    [
                        moment.model_copy(update={"end": min(moment.end, seconds)})
                        for moment in answer.moments
                    ]
                ),
                look_frames=pulled_at,
                colour=answer.colour,
                transcript=answer.transcript,
                refused=refused,
            ),
        )
    return []


def _too_long(job: Job, video: ProductVideo) -> str:
    """Tell the owner and the producer, once, that `video` is too long to be watched."""
    most = settings.WATCH_VIDEO_MOST_SECONDS
    length = f"{_seconds(video.duration or 0)} long, over the {_seconds(most)}"
    video.thrown_out = f"It is {length} the app watches."
    video.save(update_fields=["thrown_out"])
    post_notice(
        job,
        notices.PAGE_VIDEO,
        f"Your page's video {video.position} ({video.source_url}) is {length} the app watches, "
        "so it is left out of the ad.",
        Message.Level.INFO,
    )
    return f"The product's video {video.position} is {length} the app watches, so it is left out."


def _seconds(seconds: float) -> str:
    """A length as the owner reads it, in whole seconds: "24 s"."""
    return f"{round(seconds)} s"
