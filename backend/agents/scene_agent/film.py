"""Film a checked scene picture and return the clip and word checks together."""

from dataclasses import asdict
from typing import ClassVar

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from pydantic import Field

from agents.loop import Refused, Tool, ToolResult
from agents.models import ToolCall
from agents.scene_forms import VideoForm, has_quoted_text, video_prompt
from chat.models import Message
from gateway.types import ClipFailed
from jobs import (
    clip_words,
    cut_and_send,
    library,
    notices,
    scene_pictures,
    word_check,
    work,
)
from jobs.models import AgentFix, AgentFixTry, Job, ProducedItem, Scene, SceneAgentRun, SceneStep

from . import lifecycle, move_check
from . import still_shot as stills
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
    still_shot: stills.StillShot = Field(
        default=stills.NOT_STILL,
        description="Only for a planned still shot (a B-roll showcase with no hand, for a "
        'line about where the product fits or how it looks): "where it fits: pull back" or '
        '"how it looks: push in". Code then films it as a small camera move between the '
        "starting picture and a crop cut from it, around crop_x and crop_y: the crop is the "
        "first frame of a pull back and the last frame of a push in. Any end picture is not "
        "used. Write the camera move in the camera box and beats; nothing else moves.",
    )
    crop_x: int = Field(
        default=50,
        ge=0,
        le=100,
        description="Still shot only: the crop's centre, percent of the starting picture's "
        "width from its left edge: the product for a pull back, the detail the line names "
        "for a push in.",
    )
    crop_y: int = Field(
        default=50,
        ge=0,
        le=100,
        description="Still shot only: the crop's centre, percent of the starting picture's "
        "height from its top edge.",
    )

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
                if scene.kind == Scene.Kind.BROLL:
                    raise Refused(lifecycle.NEVER_FILMS_A_FAILED_PICTURE)
                failed = scene.produced.filter(
                    kind=ProducedItem.Kind.STARTING_PICTURE,
                    sent__run=window.pk,
                    ruling__status="failed",
                )
                if (
                    window.starting_pictures < window.starting_picture_limit
                    or failed.count() != window.starting_pictures
                ):
                    raise Refused(
                        f"Choose a failed picture only after all {window.starting_picture_limit} "
                        "starting pictures of this run failed the check."
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
            still = self.still_shot != stills.NOT_STILL
            if still and scene.kind != Scene.Kind.BROLL:
                raise Refused("Only a B-roll scene can be a planned still shot.")
            # A still shot's other end is a crop of the starting picture, never an end picture.
            end_picture = None if still else newest_for(picture)
            _note_a_judge_failed_pair(scene, picture, end_picture)
            if end_picture is not None and (end_picture.ruling or {}).get("status") == "failed":
                if window.end_pictures < window.end_picture_limit:
                    raise Refused(
                        "The end picture failed its check; make it again before filming. "
                        f"Only after all {window.end_picture_limit} end-picture tries are used "
                        "may a talking scene film with the last failed end picture. "
                        "Starting-picture tries do not use that budget."
                    )
                if scene.kind == Scene.Kind.BROLL:
                    raise Refused(lifecycle.NEVER_FILMS_A_FAILED_PICTURE)
            pieces = pieces_for(call, self.form)
            prompt = video_prompt(
                self.form,
                reference_mode=bool(pieces),
                end_picture=end_picture is not None or still,
                line=scene.line,
                voice_words=work.voice_words(scene.job),
                kind=scene.kind,
                hook=scene.number == 1,
                still_shot=self.still_shot,
            )
            first_file = picture.file
            last_file = end_picture.file if end_picture is not None else None
            if still:
                cut = stills.crop(picture.file, self.crop_x, self.crop_y)
                first_file, last_file = (
                    (cut, picture.file)
                    if self.still_shot == stills.PULL_BACK
                    else (picture.file, cut)
                )
            _check_the_move(call, scene, first_file, last_file, prompt)
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
                        [("Starting picture", first_file), ("End picture", last_file)],
                        1,
                    )
                ]
                if pieces and last_file
                else [],
                pieces=pieces,
                left_out=[],
                recolours=[],
                prompt=prompt,
                mode="reference" if pieces else "start_frame",
                starting_picture=library.StartingPicture(file=first_file),
                end_picture=library.StartingPicture(file=last_file) if last_file else None,
                scene_run=window.pk,
                voice_words=work.voice_words(scene.job),
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
                SceneAgentRun.objects.filter(pk=window.pk).update(clips=F("clips") + 1)
                if end_picture is not None and (end_picture.ruling or {}).get("status") == "failed":
                    notices.post_notice(
                        scene.job,
                        notices.FAILED_END_PICTURE_FILMED,
                        f"Scene {scene.number}: filming with failed end picture {end_picture.pk} "
                        f"after all {window.end_picture_limit} end-picture tries were used "
                        f"(try {row.number}).",
                        Message.Level.PROBLEM,
                    )
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
            except Exception as error:
                # A render that failed, whatever stopped it, used up its try: the last one ends
                # the run by the cap, as a last try that failed its check does.
                _end_by_the_cap_after_its_last_try(fixing, window)
                if not isinstance(error, ClipFailed):
                    raise
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
            if transcript is not None:
                clip_words.record_pace(step, clip, transcript)
            row.clip = clip
            if "compared_with" in (clip.ruling or {}):
                settled = work._the_clip_judges_answer(clip)
                row.fix_check = {"verdict": settled.verdict, "reason": settled.reason}
            row.passed = lifecycle._passes(row, clip)
            row.save(update_fields=["clip", "fix_check", "passed"])
            lifecycle._done(step)
        if self.closest_picture is not None:
            # Record it only once the clip exists, including after checkpoint recovery.
            text = (
                f"Scene {scene.number}'s starting picture never passed its check, so the closest "
                "failed picture was filmed: the clip may show what the check failed."
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


REPLAN = (
    "No clip try was used. The planned action cannot happen this way, so re-plan it: call "
    "change_scene with a new action, the way a real person really does it (for example, set "
    "the part where it needs to be before the product goes on, then put it on; show the part "
    "working on the product alone; or show only the result), with a new line if the old one "
    "no longer fits. Then draw a new starting picture for the new action, a new end picture "
    "if the clip uses one, and write the video form for it. Do not shrink the move, start it "
    "partway through, or reword the same move: the check judges the path from the pictures. "
    "This holds even when the producer's instruction names this action: a move that cannot "
    "happen cannot be filmed, so keep the instruction's point and the line's claim, change "
    "how it is shown, and say why in your reply. This counts as a failed try of the planned "
    "action: work down the ladder's steps before you give up."
)


def _check_the_move(
    call: ToolCall, scene: Scene, first: str, last: str | None, prompt: str
) -> None:
    """Refuse to film a clip whose move the move check fails, before any clip try is
    used; after MOST_MOVE_FAILS failures in this run it is filmed anyway, with a notice."""
    from . import current_run

    assert call.agent_fix is not None
    window = current_run(call.agent_fix)
    ruling = move_check.check(scene.job, scene=scene.number, first=first, last=last, prompt=prompt)
    if ruling["status"] != "failed":
        return
    answers = ruling["answers"]
    assert isinstance(answers, dict)
    fails = ToolCall.objects.filter(
        agent_fix=call.agent_fix,
        tool=Film.name,
        created_at__gte=window.created_at,
        result__contains=move_check.MOVE_FAILED,
    ).count()
    if fails + 1 >= move_check.MOST_MOVE_FAILS:
        notices.post_notice(
            scene.job,
            notices.MOVE_CHECK_FILMED_ANYWAY,
            f"Scene {scene.number}: filmed though its move check failed "
            f"{fails + 1} times in this run; last: {answers['says']}",
            Message.Level.PROBLEM,
            scene=scene.number,
        )
        return
    raise Refused(
        f"{move_check.MOVE_FAILED} {answers['says']} (It traced: {answers['moving_parts']}) "
        + REPLAN
        + lifecycle.tries_used(window.pk)
    )


def _note_a_judge_failed_pair(
    scene: Scene, picture: ProducedItem, end_picture: ProducedItem | None
) -> None:
    """Record filming `picture` with `end_picture` again when the clip judge failed the last
    clip filmed from that same pair (round 11: the same picture filmed again failed 5 of 6
    times). The scene agent is told not to; code no longer refuses it, as the refusal never
    fired in round 12 (Julian, Nov 9 2026, rules review). Only the judge's own fail counts,
    never a fix check's or a word check's."""
    end_file = end_picture.file if end_picture is not None else None
    last = next(
        (
            each
            for each in AgentFixTry.objects.filter(
                agent_fix__step__scene_id=scene.pk, picture=picture, clip__isnull=False
            )
            .select_related("clip")
            .order_by("-created_at", "-pk")
            if ((each.sent or {}).get("end_picture") or {}).get("file") == end_file
        ),
        None,
    )
    if last is None or last.clip is None or (last.clip.ruling or {}).get("verdict") != "fail":
        return
    pair = f"starting picture {picture.pk}" + (
        f" with end picture {end_picture.pk}" if end_picture is not None else ""
    )
    notices.post_notice(
        scene.job,
        notices.SAME_PICTURE_FILMED_AGAIN,
        f"Scene {scene.number}: {pair} is filmed again though the clip judge failed its last "
        f"clip (try {last.number}).",
        Message.Level.PROBLEM,
        scene=scene.number,
    )


# Why a run ended when the video model made none of its six tries.
NO_CLIP_RENDERED = "the video model couldn't make any of its 6 tries."


def _end_by_the_cap_after_its_last_try(fixing: AgentFix, window: SceneAgentRun) -> None:
    """End `window`'s run by the cap when its last try is used and its run is still the
    scene's current one."""
    if window.clips >= 6:
        _end_by_the_cap(fixing, window, NO_CLIP_RENDERED)


def _end_by_the_cap(fixing: AgentFix, window: SceneAgentRun, no_clip: str) -> None:
    """End `window`'s run by the cap, its tries for the owner to pick from, when its run is
    still the scene's current one. With no clip made in this run, there are no tries of its
    to pick from: the run ends for `no_clip` and the scene waits instead."""
    from . import current_run, finish

    with transaction.atomic():
        Job.objects.select_for_update().get(pk=fixing.step.scene.job_id)
        if current_run(fixing).pk != window.pk:
            return
        if not fixing.tries.filter(clip__isnull=False, sent__scene_run=window.pk).exists():
            finish(fixing, no_clip)
            return
        fixing.ended_by_cap = True
        fixing.save(update_fields=["ended_by_cap"])
        finish(fixing, lifecycle.ENDED_BY_CAP)


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
