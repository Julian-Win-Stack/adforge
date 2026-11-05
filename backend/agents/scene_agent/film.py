"""Film a checked scene picture and return the clip and word checks together."""

from dataclasses import asdict
from typing import ClassVar

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from pydantic import Field

from agents.loop import Refused, Tool, ToolResult
from agents.models import ToolCall
from agents.scene_forms import VideoForm, has_quoted_text, video_prompt
from chat.models import Message
from gateway.types import ClipFailed
from jobs import clip_words, cut_and_send, library, notices, scene_pictures, word_check, work
from jobs.models import AgentFix, AgentFixTry, Job, ProducedItem, Scene, SceneAgentRun, SceneStep

from . import lifecycle
from .draw_starting_picture import current_picture
from .make_end_picture import newest_for
from .photos import evidence_photos
from .reference import pieces_for


class Film(Tool):
    """Film a passed or unchecked starting picture from this scene.
    Fill the video form after looking at the picture;
    read the clip's frames and both checks, then fix the picture or film again as needed.
    """

    name: ClassVar[str] = "film"
    form: VideoForm
    picture: int | None = Field(
        default=None,
        description="Starting picture ID from this scene; omit to use the current working picture.",
    )
    closest_picture: int | None = Field(
        default=None,
        description="Only after all starting pictures failed: name the closest picture's ID.",
    )
    reason: str = Field(default="", description="Why the named failed picture is the closest.")

    def run(self, call: ToolCall) -> ToolResult:
        from . import current_run, finish

        assert call.agent_fix is not None
        fixing = call.agent_fix
        scene = fixing.step.scene
        row = fixing.tries.filter(tool_call=call).first()
        if row is None:
            window = current_run(fixing)
            if self.picture is not None and self.closest_picture is not None:
                raise Refused("Choose picture or closest_picture, not both.")
            picture = current_picture(call)
            if self.picture is not None:
                picture = scene.produced.filter(
                    kind=ProducedItem.Kind.STARTING_PICTURE, pk=self.picture
                ).first()
                if picture is None:
                    raise Refused("Name a starting picture from this scene.")
            if self.closest_picture is not None:
                failed = scene.produced.filter(
                    kind=ProducedItem.Kind.STARTING_PICTURE,
                    sent__run=window.pk,
                    ruling__status="failed",
                )
                if window.pictures < 6 or failed.count() != window.pictures:
                    raise Refused(
                        "Choose a failed picture only after all six pictures failed the check."
                    )
                if not self.reason.strip():
                    raise Refused(
                        "Say why this failed picture is the closest to the product photos."
                    )
                picture = failed.filter(pk=self.closest_picture).first()
                if picture is None:
                    raise Refused("Name a failed starting picture from this scene's current run.")
            if (
                picture is None
                or (picture.ruling or {}).get("status")
                not in {
                    "passed",
                    "unchecked",
                }
                and self.closest_picture is None
            ):
                raise Refused(
                    "Film only after the selected starting picture has passed its check "
                    "or gone unchecked."
                )
            if not scene_pictures.matches(picture, scene):
                raise Refused("The starting picture is out of date; draw and check a new picture.")
            window = current_run(fixing)
            if window.clips >= 6:
                raise Refused("This run has finished filming its clips.")
            # Code's fixed lines are exempt; only the agent's form boxes are checked.
            boxes: list[str] = []
            for value in self.form.model_dump().values():
                boxes.extend(value if isinstance(value, list) else [value])
            if any(has_quoted_text(box) for box in boxes):
                raise Refused(
                    "The video form has quoted text. "
                    "Describe on-screen text instead of quoting it; code supplies the spoken line. "
                    "Rewrite the form before filming."
                )
            lifecycle._validate_line(scene.line)
            pieces = pieces_for(call, self.form)
            end_picture = newest_for(picture)
            prompt = video_prompt(
                self.form,
                reference_mode=bool(pieces),
                end_picture=end_picture is not None,
                line=scene.line,
                voice_words=scene.job.person_voice,
                kind=scene.kind,
            )
            selected = evidence_photos(picture)
            photos = [photo for photo in selected if photo.library_number is not None]
            sent = library.SentList(
                colour=cut_and_send.planned_colour(scene),
                pictures=[
                    library.SentPicture(
                        image=number,
                        tier=3,
                        library_number=None,
                        drawn=work.drawn_for(scene.person_shown),
                        for_=None,
                        label=label,
                        file=file,
                    )
                    for number, (label, file) in enumerate(
                        [("Starting picture", picture.file), ("End picture", end_picture.file)],
                        1,
                    )
                ]
                if pieces and end_picture
                else [],
                pieces=pieces,
                left_out=[],
                recolours=[],
                prompt=prompt,
                mode="reference" if pieces else "start_frame",
                starting_picture=library.StartingPicture(file=picture.file),
                end_picture=library.StartingPicture(file=end_picture.file) if end_picture else None,
                scene_run=window.pk,
                voice_words=work._how_the_voice_sounds(scene.job),
                picture_photos=[
                    library.SentPicture(
                        image=number,
                        tier=2,
                        library_number=photo.library_number,
                        drawn=None,
                        for_=None,
                        label=photo.line,
                        file=photo.file,
                    )
                    for number, photo in enumerate(photos, 1)
                ],
            )
            with transaction.atomic():
                step = lifecycle._try_step(
                    fixing,
                    call,
                    SceneStep.Kind.CLIP,
                    picture=picture,
                    motion_prompt=prompt,
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
                SceneAgentRun.objects.filter(pk=window.pk).update(clips=window.clips + 1)
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
                if sent.end_picture is not None:
                    text = f"H3 refused scene {scene.number}'s clip with an end picture: {error}"
                    notices.post_notice(
                        scene.job, notices.END_PICTURE_CLIP_REFUSED, text, Message.Level.PROBLEM
                    )
                    raise Refused(text) from error
                raise
            ProducedItem.objects.filter(pk=clip.pk).update(
                remake=row.number, change=window.instruction
            )
            clip.refresh_from_db()
            transcript = work.transcribe_clip(step, clip)
            if settings.QUALITY_CHECKS:
                if transcript is not None:
                    clip_words.trim_leading_words(step, clip, transcript)
                if "word_check" not in (clip.ruling or {}):
                    work._judge_clip(step, clip)
                    clip.refresh_from_db()
                _check_words(step, clip, transcript)
            row.clip = clip
            if "compared_with" in (clip.ruling or {}):
                settled = work._the_clip_judges_answer(clip)
                row.fix_check = {"verdict": settled.verdict, "reason": settled.reason}
            row.passed = lifecycle._passes(row, clip)
            row.save(update_fields=["clip", "fix_check", "passed"])
            lifecycle._done(step)
        if self.closest_picture is not None:
            # Tell the owner only once the clip exists, including after checkpoint recovery.
            text = (
                f"Scene {scene.number}'s picture never passed the check; we filmed the closest one."
            )
            with transaction.atomic():
                locked = Job.objects.select_for_update().get(pk=scene.job_id)
                if not any(
                    warning.get("kind") == notices.CLOSEST_PICTURE_FILMED.name
                    and warning.get("text") == text
                    and warning.get("at", "") >= window.created_at.isoformat()
                    for warning in locked.warnings
                ):
                    notices.post_notice(
                        locked, notices.CLOSEST_PICTURE_FILMED, text, Message.Level.PROBLEM
                    )
        with transaction.atomic():
            Job.objects.select_for_update().get(pk=scene.job_id)
            # An in-flight clip belongs to the window that submitted it. A newer note
            # stays active, including when this old clip passed or used its last try.
            if current_run(fixing).pk != window.pk:
                return lifecycle._told(fixing, row)
            if row.passed:
                AgentFix.objects.filter(pk=fixing.pk).update(
                    passed=row.clip, finished_at=timezone.now()
                )
                SceneAgentRun.objects.filter(pk=window.pk).update(finished_at=timezone.now())
                Scene.objects.filter(pk=scene.pk).update(status=Scene.Status.FINISHED)
            elif window.clips >= 6:
                fixing.ended_by_cap = True
                fixing.save(update_fields=["ended_by_cap"])
                finish(fixing, lifecycle.ENDED_BY_CAP)
        return lifecycle._told(fixing, row)


def _check_words(step: SceneStep, clip: ProducedItem, transcript: ProducedItem | None) -> None:
    """Keep this clip's word result once; an unavailable check never holds up the ad."""
    if "word_check" in (clip.ruling or {}):
        return
    scene = step.scene
    result = word_check.check(scene.job, step.line, transcript.text if transcript else None)
    with transaction.atomic():
        clip.ruling = {**(clip.ruling or {}), "word_check": asdict(result)}
        clip.save(update_fields=["ruling"])
        if result.verdict == "unchecked":
            notices.ask_to_watch(
                clip,
                f"Scene {scene.number}'s spoken words could not be checked. Please listen to "
                "the clip yourself. It is kept, and the ad goes on without waiting for you.",
            )
