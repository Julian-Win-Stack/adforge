"""H3 clips play whole with their own sound and captions from the written script."""

from collections.abc import Callable
from pathlib import Path

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from adforge import file_store
from agents.models import ToolCall
from gateway.fake import FakeModel, turn
from jobs import assembly
from jobs.models import Job, ProducedItem, Scene, SceneStep

from .conftest import chat, loudness, results_of, video
from .test_assembly import clip, music


def test_whole_clips_keep_their_sound_in_order() -> None:
    cuts = assembly.whole_clips([(1, 11, 8.5, "First"), (2, 12, 10.0, "")])

    assert cuts == [
        assembly.Cut(1, 11, 0.0, 8.5, 0.0, 8.5, "First", 0.0, 8.5, 0.0, 8.5),
        assembly.Cut(2, 12, 0.0, 10.0, 8.5, 18.5, "", 0.0, 10.0, 8.5, 18.5),
    ]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("missing_transcript", [False, True])
def test_the_chat_assembles_whole_clips_and_written_captions(
    fake_model: FakeModel,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    tmp_path: Path,
    missing_transcript: bool,
) -> None:
    job = Job.objects.create(session_id=session_id, h3_voice=True)
    made = []
    for number, (line, heard, seconds) in enumerate(
        [("Show our mug.", "Show your mug.", 5.0), ("Get it today.", "Get it today.", 6.0)],
        1,
    ):
        scene = job.scenes.create(
            number=number,
            line=line,
            kind=Scene.Kind.TALKING if number == 1 else Scene.Kind.BROLL,
            shows="She shows the mug.",
            status=Scene.Status.FINISHED,
        )
        call = ToolCall.objects.create(
            session_id=session_id,
            job=job,
            agent="producer",
            tool="start_scene_agent",
            call_id=str(number),
            arguments={},
            finished_at=timezone.now(),
        )
        step = scene.steps.create(
            tool_call=call,
            kind=SceneStep.Kind.CLIP,
            status=SceneStep.Status.FINISHED,
            **SceneStep.snapshot(scene),
        )
        path = clip(tmp_path, f"scene-{number}", seconds=seconds)
        made_clip = scene.produced.create(
            job=job,
            step=step,
            kind=ProducedItem.Kind.CLIP,
            version=1,
            file=file_store.save(path.name, path.read_bytes()),
            seconds=seconds,
        )
        made.append(made_clip)
        if missing_transcript and number == 2:
            continue
        transcript_step = scene.steps.create(
            tool_call=call,
            kind=SceneStep.Kind.TRANSCRIPT,
            status=SceneStep.Status.FINISHED,
            **SceneStep.snapshot(scene),
        )
        scene.produced.create(
            job=job,
            step=transcript_step,
            kind=ProducedItem.Kind.TRANSCRIPT,
            version=1,
            made_from=made_clip,
            text=heard,
            words=[
                {"text": word, "start": 1.0 + at * 0.5, "end": 1.5 + at * 0.5}
                for at, word in enumerate(heard.split())
            ],
        )
    music_path = music(tmp_path, seconds=11)
    job.produced.create(
        kind=ProducedItem.Kind.MUSIC,
        version=1,
        file=file_store.save("music.wav", music_path.read_bytes()),
        seconds=11,
    )
    fake_model.respond("produce", turn(calls=[("assemble_ad", {})]), turn(says="Here's your ad."))

    say("Put the ad together")

    ad = job.produced.get(kind=ProducedItem.Kind.FINISHED_AD)
    assert ad.seconds == 11
    assert [cut["clip"] for cut in ad.cuts] == [item.pk for item in made]
    assert [(cut["clip_start"], cut["clip_end"]) for cut in ad.cuts] == [(0, 5), (0, 6)]
    assert [(cut["voice_start"], cut["voice_end"]) for cut in ad.cuts] == [(0, 5), (5, 11)]
    expected = [
        {"text": "Show our mug.", "start": 1.0, "end": 2.5},
        {"text": "Get it today.", "start": 6.0, "end": 7.5},
    ]
    assert ad.captions == (expected[:1] if missing_transcript else expected)
    data = file_store.read(ad.file)
    assert video(data)[2] == pytest.approx(11, abs=0.1)
    for start, end in [(0.2, 0.8), (5.2, 5.8), (10.2, 10.8)]:
        assert loudness(data, "voice", between=(start, end)) > -40
        assert loudness(data, "music", between=(start, end)) > -40
    # A fifth of the original music's amplitude is 14 dB lower.
    assert loudness(data, "music", between=(2, 3)) == pytest.approx(
        loudness(music_path.read_bytes(), "music", between=(2, 3)) - 14, abs=1
    )
    assert "each clip whole with its own sound" in results_of("assemble_ad")[-1]
    assert "over the end" not in results_of("assemble_ad")[-1]
    assert chat(api, session_id)[-1] == ("agent", "Here's your ad.")
