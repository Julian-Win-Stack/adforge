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
it."""

import contextvars
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Self

from django.conf import settings
from pydantic import BaseModel, ConfigDict, Field, model_validator

from adforge import file_store
from chat.models import Message
from gateway.gateway import watch_video
from gateway.types import Handoff, UnusableReply
from jobs import page
from jobs.library import (
    VIDEO_API_MOST_PIECE_SECONDS,
    Colour,
    LookFrame,
    PictureLabel,
    VideoWatch,
    WatchedMoment,
    add_to_library,
    keep_watch,
    number_moments,
)
from jobs.models import Job, ProductVideo
from jobs.notices import post_notice
from jobs.product_videos import frame_of
from jobs.threads import closing_its_connection
from jobs.work import save_video_pictures

logger = logging.getLogger(__name__)

PURPOSE = "watch_video"
# A video's transcript when nothing is said in it, as the planner has always been told.
MUSIC_ONLY = "music only"
# An answer the schema refuses is asked for again this many times.
ASKED_AGAIN = 1

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

Transcript: what is said in the video, word for word; empty when nothing is said."""


class WatcherHandoff(Handoff):
    """What the watcher is handed beside the video: the page and the video's length."""

    product_url: str
    page_text: str
    seconds: float


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
    """The watcher's answer for a video `seconds` long: nothing in it past the video's end."""

    class WatcherAnswerForThisVideo(WatcherAnswer):
        @model_validator(mode="after")
        def _inside_the_video(self) -> Self:
            ends = [moment.end for moment in self.moments]
            ends += [frame.second for frame in self.look_frames]
            if any(end > seconds for end in ends):
                raise ValueError(f"the video is {seconds:g} s long; nothing is past its end")
            return self

    WatcherAnswerForThisVideo.__name__ = WatcherAnswer.__name__
    return WatcherAnswerForThisVideo


def watch_videos(job: Job, page_text: str) -> list[str]:
    """Watch each of the job's videos with a file that wasn't watched yet, all at once, and
    keep what was seen. Gives back a line for the producer for each video not watched or
    that couldn't be."""
    waiting = [
        video
        for video in job.videos.exclude(file="").order_by("position")
        if video.watched is None and not video.thrown_out
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
                answer = each.result()
            except Exception as error:
                # Whatever stops one video's watch, the page is read and the ad planned
                # without it: never quietly.
                logger.exception("Product video %s couldn't be watched", video.pk)
                post_notice(
                    job,
                    f"Your product's video {video.position} couldn't be watched, so the ad is "
                    "planned without it.",
                    Message.Level.PROBLEM,
                )
                told.append(
                    f"The product's video {video.position} couldn't be watched ({error}), so "
                    "the ad is planned without it."
                )
                continue
            told += _keep(job, video, answer)
    return told


def _watch(job: Job, video: ProductVideo, page_text: str) -> WatcherAnswer:
    """The watcher's answer for `video`, asked again ASKED_AGAIN times when its answer is
    refused by the schema."""
    seconds = float(video.duration or 0)
    handoff = WatcherHandoff(
        product_url=job.product_url, page_text=page.for_model(page_text), seconds=seconds
    )
    for asked in range(ASKED_AGAIN + 1):
        try:
            return watch_video(
                job=job,
                purpose=PURPOSE,
                instructions=INSTRUCTIONS,
                handoff=handoff,
                video_key=video.file,
                output=_answer_for(seconds),
            )
        except UnusableReply:
            if asked == ASKED_AGAIN:
                raise
    raise AssertionError("unreachable")


def _keep(job: Job, video: ProductVideo, answer: WatcherAnswer) -> list[str]:
    """Keep what the watcher saw in `video`: its moments and transcript on it, its look frames
    in the library and as video pictures. A video that doesn't show this product is thrown
    out instead. Gives back what the producer is told."""
    if not answer.shows_this_product:
        video.thrown_out = "The watcher said it doesn't show the product the page sells."
        video.save(update_fields=["thrown_out"])
        post_notice(
            job,
            f"Your page's video {video.position} doesn't show this product, so it is left out "
            "of the ad.",
            Message.Level.INFO,
        )
        return [f"The product's video {video.position} doesn't show this product: left out."]
    keep_watch(
        video,
        VideoWatch(
            moments=number_moments(answer.moments),
            look_frames=[frame.second for frame in answer.look_frames],
            colour=answer.colour,
            transcript=answer.transcript,
        ),
    )
    video.transcript = answer.transcript.strip() or MUSIC_ONLY
    video.save(update_fields=["transcript"])
    pulled = []
    for number, frame in enumerate(answer.look_frames, 1):
        key = file_store.save(
            f"jobs/{job.pk}/videos/{video.position}-look-{number}.jpg",
            frame_of(video, frame.second),
        )
        label = PictureLabel.model_validate(frame.model_dump(exclude={"second"}))
        add_to_library(job, key, label, LookFrame(video=video.position, second=frame.second))
        pulled.append({"key": key, "second": frame.second, "shows": frame.label})
    # The planner's video pictures, until it reads the library: the same look frames.
    save_video_pictures(job, video, pulled, pulled)
    return []


def _too_long(job: Job, video: ProductVideo) -> str:
    """Tell the owner and the producer that `video` is too long to be watched."""
    most = settings.WATCH_VIDEO_MOST_SECONDS
    length = f"{_seconds(video.duration or 0)} long, over the {_seconds(most)}"
    post_notice(
        job,
        f"Your page's video {video.position} ({video.source_url}) is {length} the app watches, "
        "so it is left out of the ad.",
        Message.Level.INFO,
    )
    return f"The product's video {video.position} is {length} the app watches, so it is left out."


def _seconds(seconds: float) -> str:
    """A length as the owner reads it, in whole seconds: "24 s"."""
    return f"{round(seconds)} s"
