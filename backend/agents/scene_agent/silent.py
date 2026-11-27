"""The scene agent's tools and instructions for a silent scene: a product-shots ad's scene,
with no presenter and no spoken line (#770, #772). Its starting picture is the shop's real
product photo fitted to the tall frame, or an AI-made scene drawn from the product photos; its
clip is a slow camera move between that picture and a crop of it, checked by the existing
picture check and clip judge."""

import math
from typing import ClassVar, Literal

from django.db import transaction
from django.db.models import F, Max
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field

from agents.loop import Refused, Tool, ToolResult
from agents.models import ToolCall
from agents.scene_forms import PRODUCT_DESIGN_LINE, PULL_BACK, PUSH_IN, has_quoted_text
from chat import messages
from chat.models import Attachment, Message
from gateway.gateway import edit_picture
from gateway.types import BlockedBySafetyFilter, ClipFailed, Image
from jobs import cut_and_send, library, library_items, notices, real_photo, work
from jobs.models import AgentFix, AgentFixTry, Job, ProducedItem, Scene, SceneAgentRun, SceneStep

from . import lifecycle
from . import still_shot as stills
from .media import _frames, judge_words

# The video model makes clips of at least this many whole seconds.
SHORTEST_CLIP = 5
LONGEST_CLIP = 10

Move = Literal["push in", "pull back"]

SILENT_INSTRUCTIONS = """\
You own one silent scene of a product-shots ad: tall, no presenter, no spoken line, no voice. \
The ad is beautiful product shots, overlays drawn by code and music. Your scene's plan says \
what it shows, the library photo it starts on, whether it is "real photo" or "ai scene", the \
camera move and its length.

A "real photo" scene: call use_real_photo with the planned photo. Code fits the shop's own \
photo to the tall frame and keeps its pixels, so the buyer sees the exact product. Give \
focus_x and focus_y on the product (percent of the photo's width and height) so a crop keeps \
it. If code can't fit the photo, it says so and you draw an AI scene instead.
An "ai scene": call draw_silent_picture with picks of the product photos (at least the \
planned one) and the picture form. Draw the product exactly as the photos show it, large in \
the frame, whole, in a setting that matches the product's style and the plan. A part no \
photo shows is drawn in its most ordinary look. No presenter; a hand or a worn product on a \
body is fine when the plan says so, with no face unless the photo it starts on shows one.

Then film_silent: the clip is a slow camera move between the starting picture and a crop of \
it around crop_x, crop_y, so the product can't drift: "push in" ends close on the detail, \
"pull back" starts close on it. Light may move over the product (a slow sweep, a glint). \
Nothing else moves: never ask the product to turn, swing, open or lift. Write no spoken \
words and no quoted text in any box. Read the clip judge's answer and the frames: when it \
fails, fix the cause (a new picture, a different crop centre or move, a shorter light \
description) and film again. Every picture and clip result says how many tries are left. \
Call give_up with a plain reason, for the producer only, once nothing more can be tried. \
Never ask the owner for anything. A turn must call a tool."""


class SilentPictureForm(BaseModel):
    """The AI-made scene's picture, box by box. Code puts each picked photo's job first and
    adds the fixed ending; the picture is drawn tall."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    setting: str = Field(
        description="Where the product is and what is around it, matching the product's "
        'style. A made-up example: "on smooth pale stone beside a sunlit window".'
    )
    product: str = Field(
        description="How the product sits in the frame: whole, large, which side to camera, "
        "held or worn if the plan says so."
    )
    light: str = Field(description="Where the light comes from and how it falls on the product.")
    framing: str = Field(description='The framing, such as "close-up, product fills the middle".')
    anything_else: str = Field(description='Anything else the picture needs, or "not needed".')


class Pick(BaseModel):
    item: int = Field(description="A library photo number.")
    take: str = Field(description="One line on what to take from this photo.")


class SilentVideoForm(BaseModel):
    """The clip's prompt, box by box. Code adds the speed, first-frame and camera lines and
    "no speech" at the end."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    frame_shows: str = Field(description="What the starting picture shows, in plain words.")
    product: str = Field(description="The product in one phrase: colour, material and shape.")
    light: str = Field(
        description="How light moves over the product during the move, such as a slow soft "
        'sweep of light across the stone, or "not needed".'
    )
    lock_line: str = Field(
        description='Last: "The [product] keeps its exact shape, colour and details." After a '
        "failed clip it grows: every part that must keep its shape."
    )


def is_silent(scene: Scene) -> bool:
    return scene.kind == Scene.Kind.SILENT


def silent_plan(scene: Scene) -> dict[str, object]:
    return dict(scene.silent or {})


def scene_facts(scene: Scene) -> str:
    """A silent scene as the scene agent reads it in the whole plan."""
    plan = silent_plan(scene)
    facts = [
        f"Scene {scene.number} (silent)",
        f"  shows: {scene.shows}",
        f"  starts on: library item {plan.get('photo')} ({plan.get('start')})",
        f"  camera move: {plan.get('move')}",
        f"  length: {plan.get('seconds')} s",
    ]
    if scene.overlay:
        facts.append(f'  overlay: "{scene.overlay}" (code draws this on the finished ad)')
    return "\n".join(facts)


def clip_seconds(scene: Scene) -> int:
    """The whole seconds the video model films for the scene: its planned length, rounded
    up, at least the shortest clip the model makes. Assembly cuts it to the planned length."""
    seconds = float(str(silent_plan(scene).get("seconds") or 3))
    return min(LONGEST_CLIP, max(SHORTEST_CLIP, math.ceil(seconds)))


def _window(call: ToolCall) -> tuple[AgentFix, SceneAgentRun, Scene]:
    from . import current_run

    assert call.agent_fix is not None
    fixing = call.agent_fix
    return fixing, current_run(fixing), fixing.step.scene


def _photo(job: Job, number: int) -> library.LibraryEntry:
    found = {item.number: item for item in library_items.originals(job)}
    if number not in found:
        raise Refused(f"there is no library photo {number}; name one from the library.")
    return found[number]


def _keep_picture(
    call: ToolCall,
    scene: Scene,
    window: SceneAgentRun,
    key: str,
    *,
    prompt: str,
    ruling: dict[str, object],
    photos: list[library.LibraryEntry],
    how: str,
) -> ProducedItem:
    with transaction.atomic():
        version = (
            scene.produced.filter(kind=ProducedItem.Kind.STARTING_PICTURE).aggregate(
                last=Max("version")
            )["last"]
            or 0
        ) + 1
        assert call.agent_fix is not None
        made = ProducedItem.objects.create(
            job=scene.job,
            scene=scene,
            step=call.agent_fix.step,
            kind=ProducedItem.Kind.STARTING_PICTURE,
            file=key,
            version=version,
            text=prompt,
            change=window.instruction if version > 1 else "",
            ruling=ruling,
            sent={
                "run": window.pk,
                "call": str(call.pk),
                "silent": how,
                "photos": [
                    {"file": photo.file, "line": photo.label, "library_number": photo.number}
                    for photo in photos
                ],
                "scene": {"line": scene.line, "shows": scene.shows},
                "kind": scene.kind,
            },
        )
        SceneAgentRun.objects.filter(pk=window.pk).update(
            starting_pictures=F("starting_pictures") + 1
        )
        if not scene.produced.filter(kind=ProducedItem.Kind.CLIP).exists():
            messages.add(
                call.session,
                role=Message.Role.AGENT,
                carrying=[
                    messages.AttachedFile(
                        Attachment.Kind.PICTURE, made.file, scene.number, work.version_label(made)
                    )
                ],
            )
    return made


def _picture_result(made: ProducedItem) -> ToolResult:
    return ToolResult(
        f"Starting picture {made.pk} ({(made.sent or {}).get('silent')}): "
        f"{made.ruling}" + lifecycle.tries_used((made.sent or {}).get("run")),
        [Image(label=f"Starting picture {made.pk}", key=made.file)],
    )


def _ruling(problems: list[str]) -> dict[str, object]:
    return {"status": "failed", "problems": problems} if problems else {"status": "passed"}


class UseRealPhoto(Tool):
    """Start this silent scene on the shop's own product photo: code fits it to the tall
    frame and keeps its pixels (cropped, or with the background extended above and below)."""

    name: ClassVar[str] = "use_real_photo"
    photo: int = Field(description="The library number of the product photo to start on.")
    focus_x: int = Field(
        default=50, description="The product's centre, percent of the photo's width from left."
    )
    focus_y: int = Field(
        default=50, description="The product's centre, percent of the photo's height from top."
    )

    def run(self, call: ToolCall) -> ToolResult:
        fixing, window, scene = _window(call)
        old = scene.produced.filter(
            kind=ProducedItem.Kind.STARTING_PICTURE, sent__call=str(call.pk)
        ).first()
        if old is not None:
            return _picture_result(old)
        if window.starting_pictures >= window.starting_picture_limit:
            raise Refused(lifecycle.picture_tries_used_up(scene, window, "starting"))
        item = _photo(scene.job, self.photo)
        try:
            fitted = real_photo.fit(scene.job, item.file, self.focus_x, self.focus_y)
        except real_photo.PhotoNotFitted as error:
            notices.post_notice(
                scene.job,
                notices.REAL_PHOTO_NOT_FITTED,
                f"Scene {scene.number} couldn't start on your photo ({error}), so it starts on "
                "a picture drawn from your photos instead.",
                Message.Level.PROBLEM,
                scene=scene.number,
            )
            raise Refused(
                f"photo {self.photo} can't be fitted to the tall frame: {error}. Draw an AI "
                "scene from the photos with draw_silent_picture instead."
            ) from error
        except (real_photo.PixelsNotPasted, BlockedBySafetyFilter, ClipFailed) as error:
            notices.post_notice(
                scene.job,
                notices.PIXELS_NOT_PASTED_BACK,
                f"Scene {scene.number}: photo {self.photo}'s pixels couldn't be pasted back over "
                f"its extended background ({error}).",
                Message.Level.PROBLEM,
                scene=scene.number,
            )
            notices.post_notice(
                scene.job,
                notices.REAL_PHOTO_NOT_FITTED,
                f"Scene {scene.number} couldn't start on your photo as it is, so it starts on "
                "a picture drawn from your photos instead.",
                Message.Level.PROBLEM,
                scene=scene.number,
            )
            raise Refused(
                f"photo {self.photo}'s background couldn't be extended ({error}). Draw an AI "
                "scene from the photos with draw_silent_picture instead."
            ) from error
        if fitted.how == "extended":
            # The extended bands are drawn: the existing check compares the picture with the
            # shop's photo.
            problems = work.judge_starting_picture(
                scene.job, fitted.file, item.file, about=f"Scene {scene.number}"
            )
            ruling = _ruling(problems)
        else:
            ruling = {"status": "passed", "why": "the shop's own photo, cropped by code"}
        made = _keep_picture(
            call,
            scene,
            window,
            fitted.file,
            prompt=real_photo.EXTEND_PROMPT if fitted.how == "extended" else "",
            ruling=ruling,
            photos=[item],
            how=f"real photo, {fitted.how}",
        )
        return _picture_result(made)


class DrawSilentPicture(Tool):
    """Draw an AI-made starting picture for this silent scene from the product photos you
    pick, filling the picture form. The existing picture check answers; you see the picture."""

    name: ClassVar[str] = "draw_silent_picture"
    picks: list[Pick] = Field(min_length=1, max_length=4)
    form: SilentPictureForm

    def run(self, call: ToolCall) -> ToolResult:
        fixing, window, scene = _window(call)
        old = scene.produced.filter(
            kind=ProducedItem.Kind.STARTING_PICTURE, sent__call=str(call.pk)
        ).first()
        if old is not None:
            return _picture_result(old)
        if window.starting_pictures >= window.starting_picture_limit:
            raise Refused(lifecycle.picture_tries_used_up(scene, window, "starting"))
        items = [_photo(scene.job, pick.item) for pick in self.picks]
        if any(not pick.take.strip() for pick in self.picks):
            raise Refused("say what to take from each picked photo.")
        jobs = [
            f"Image {number}: {pick.take.strip().rstrip('.')}."
            for number, pick in enumerate(self.picks, 1)
        ]
        boxes = [
            self.form.setting,
            self.form.product,
            self.form.light,
            self.form.framing,
            self.form.anything_else,
        ]
        prompt = " ".join(
            [
                "A tall vertical product photograph for a premium ad, no people unless said.",
                *jobs,
                *(
                    box.strip().rstrip(".") + "."
                    for box in boxes
                    if box.strip() and box.strip().lower().rstrip(".") != "not needed"
                ),
                f"Draw the product exactly as the photos show it. {PRODUCT_DESIGN_LINE} No "
                "text, no logo, no watermark.",
            ]
        )
        try:
            key = edit_picture(
                job=scene.job,
                purpose="draw_scene_picture",
                prompt=prompt,
                pictures=[item.file for item in items],
            )
        except BlockedBySafetyFilter:
            notices.post_refused_by_the_filter(
                scene.job, "the scene's selected photos", "no starting picture was drawn"
            )
            raise Refused("the picture model's safety filter refused these photos.") from None
        problems = work.judge_starting_picture(
            scene.job, key, items[0].file, about=f"Scene {scene.number}"
        )
        made = _keep_picture(
            call,
            scene,
            window,
            key,
            prompt=prompt,
            ruling=_ruling(problems),
            photos=items,
            how="ai scene",
        )
        return _picture_result(made)


def silent_prompt(form: SilentVideoForm, move: Move) -> str:
    camera = (
        "Only the camera moves: a slow, smooth push in from the whole frame to a close view "
        "of the detail in the last frame."
        if move == "push in"
        else "Only the camera moves: a slow, smooth pull back from a close view of the "
        "product to the whole frame of the last frame."
    )

    def box(text: str) -> str:
        text = " ".join(text.split())
        if not text or text.lower().rstrip(".") in ("not needed", "not needed here"):
            return ""
        return text if text.endswith(".") else f"{text}."

    return " ".join(
        filter(
            None,
            [
                "Film this in real-time speed.",
                "Movement starts from the very first frame, never a still opening.",
                "Premium vertical product video, close-up.",
                box(form.frame_shows),
                box(form.product),
                camera,
                box(form.light),
                "The product and everything around it stay exactly where they are; nothing "
                "turns, swings, opens or lifts.",
                box(form.lock_line),
                "Sound: none. No speech, no voices, no music.",
            ],
        )
    )


class FilmSilent(Tool):
    """Film a passed starting picture of this silent scene as a slow camera move between
    the picture and a crop of it around crop_x, crop_y. The clip judge answers; you see its
    frames."""

    name: ClassVar[str] = "film_silent"
    picture: int | None = Field(
        default=None, description="Starting picture ID; omit for the newest picture."
    )
    move: Move = Field(description='"push in" or "pull back", as the plan says.')
    crop_x: int = Field(
        default=50, ge=0, le=100, description="The crop's centre: percent of width from left."
    )
    crop_y: int = Field(
        default=50, ge=0, le=100, description="The crop's centre: percent of height from top."
    )
    form: SilentVideoForm

    def run(self, call: ToolCall) -> ToolResult:
        from . import current_run, finish

        fixing, window, scene = _window(call)
        row = fixing.tries.filter(tool_call=call).first()
        if row is None:
            pictures = scene.produced.filter(kind=ProducedItem.Kind.STARTING_PICTURE)
            picture = (
                pictures.filter(pk=self.picture).first()
                if self.picture is not None
                else pictures.order_by("-created_at", "-pk").first()
            )
            if picture is None:
                raise Refused("make a starting picture first.")
            if (picture.ruling or {}).get("status") not in ("passed", "unchecked"):
                raise Refused("film only a starting picture that passed its check.")
            if window.clips >= lifecycle.clip_limit(window):
                raise Refused("this run has filmed all its clips.")
            if any(has_quoted_text(box) for box in self.form.model_dump().values()):
                raise Refused("the video form has quoted text; a silent clip has no words.")
            first, last = stills.frames(
                picture.file,
                PUSH_IN if self.move == "push in" else PULL_BACK,
                self.crop_x,
                self.crop_y,
            )
            prompt = silent_prompt(self.form, self.move)
            colour = cut_and_send.planned_colour(scene) or "none"
            used = (picture.sent or {}).get("photos") or [
                {"library_number": item.number, "file": item.file}
                for item in library_items.originals(scene.job)[:1]
            ]
            references = [
                library.SentPicture(
                    image=number,
                    tier=2,
                    library_number=int(each["library_number"]),
                    drawn=None,
                    for_=None,
                    label=f"Library item {each['library_number']}: the real product",
                    file=str(each["file"]),
                )
                for number, each in enumerate(used, 1)
            ]
            sent = library.SentList(
                colour=colour,
                pictures=[],
                pieces=[],
                left_out=[],
                recolours=[],
                prompt=prompt,
                mode="start_frame",
                starting_picture=library.StartingPicture(file=first),
                end_picture=library.StartingPicture(file=last),
                scene_run=window.pk,
                picture_photos=references,
                seconds=clip_seconds(scene),
            )
            with transaction.atomic():
                step = lifecycle._try_step(
                    fixing, call, SceneStep.Kind.CLIP, picture=picture, motion_prompt=prompt
                )
                row = AgentFixTry.objects.create(
                    agent_fix=fixing,
                    number=fixing.tries.count() + 1,
                    tool_call=call,
                    step=step,
                    picture=picture,
                    scene_row=lifecycle._row(scene),
                    video_prompt=prompt,
                    sent=sent.model_dump(mode="json"),
                )
                SceneAgentRun.objects.filter(pk=window.pk).update(clips=F("clips") + 1)
        assert row.step is not None and row.sent is not None
        step = row.step
        sent = library.SentList.model_validate(row.sent)
        assert sent.scene_run is not None
        window = SceneAgentRun.objects.get(pk=sent.scene_run)
        if row.clip is None:
            try:
                clip = step.produced.filter(kind=ProducedItem.Kind.CLIP).first() or work._make_clip(
                    step, motion=row.video_prompt, sent=sent
                )
            except ClipFailed as error:
                window.refresh_from_db()
                if lifecycle.clips_used_up(window):
                    finish(fixing, f"the video model couldn't make the clip: {error}")
                raise Refused(f"the video model couldn't make this clip: {error}") from error
            ProducedItem.objects.filter(pk=clip.pk).update(
                remake=row.number, change=window.instruction
            )
            clip.refresh_from_db()
            from django.conf import settings

            if settings.QUALITY_CHECKS and "verdict" not in (clip.ruling or {}):
                work._judge_clip(step, clip)
                clip.refresh_from_db()
            row.clip = clip
            row.save(update_fields=["clip"])
            lifecycle._done(step)
        row.passed = lifecycle.passes(row)
        row.save(update_fields=["passed"])
        with transaction.atomic():
            Job.objects.select_for_update().get(pk=scene.job_id)
            if current_run(fixing).pk != window.pk:
                return _told(row)
            window.refresh_from_db()
            if row.passed:
                AgentFix.objects.filter(pk=fixing.pk).update(
                    passed=row.clip, finished_at=timezone.now()
                )
                SceneAgentRun.objects.filter(pk=window.pk).update(finished_at=timezone.now())
                assert row.clip is not None
                work.finish_with_clip(scene, row.clip)
            elif lifecycle.clips_used_up(window):
                fixing.ended_by_cap = True
                fixing.save(update_fields=["ended_by_cap"])
                finish(fixing, lifecycle.ENDED_BY_CAP)
        return _told(row)


def _told(row: AgentFixTry) -> ToolResult:
    assert row.clip is not None
    label = f"Try {row.number}"
    pictures = [
        Image(label=each.label, key=each.key) for each in _frames(row.clip, f"{label}, new clip")
    ]
    words = [f"{label}.", judge_words(row.clip.ruling), "It passes." if row.passed else "It fails."]
    run = (row.sent or {}).get("scene_run")
    return ToolResult("\n".join(words) + lifecycle.tries_used(run), pictures)
