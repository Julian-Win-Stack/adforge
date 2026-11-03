"""PROTOTYPE, never merged: run the B-roll scene agent on test scenes, stage 1 (stills and
prompts). Each scene's run goes to <out>/<slug>/ (run.json and its pictures).

    DATABASE_URL=... uv run python -m scene_proto.run <scenes dir> <out dir> [slug ...] [--fake]

--fake runs every scene free: the agent's turns are scripted, pictures are a grey card, and
the check fails the first picture, then passes."""

import os
import sys
import threading
from pathlib import Path
from typing import Any

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")
django.setup()

from django.db import connection  # noqa: E402

from adforge import file_store  # noqa: E402
from agents import loop, scene_agent  # noqa: E402


def _copy_pictures(run: scene_agent.Run) -> None:
    for picture in run.pictures:
        target = run.out / f"picture-{picture.number}.png"
        if not target.exists():
            target.write_bytes(file_store.read(picture.key))


def one(folder: Path, out: Path) -> None:
    try:
        inputs = scene_agent.load(folder)
        run = scene_agent.make(inputs, out, on_step=_copy_pictures)
        _copy_pictures(run)
        print(f"{inputs.slug}: {run.ended}; {len(run.pictures)} pictures; ${scene_agent.cost(run)}")
    except Exception as error:  # a scene that breaks never stops the others
        print(f"{folder.name}: FAILED {type(error).__name__}: {error}")
        raise
    finally:
        connection.close()


def main(argv: list[str]) -> None:
    fake = "--fake" in argv
    args = [each for each in argv if each != "--fake"]
    scenes, out = Path(args[0]), Path(args[1])
    slugs = args[2:] or sorted(p.name for p in scenes.iterdir() if (p / "scene.json").exists())
    if fake:
        _fake()
    threads = [
        threading.Thread(target=one, args=(scenes / slug, out / slug), name=slug) for slug in slugs
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


# ---------------------------------------------------------------- the free run


def _fake() -> None:
    import io

    import PIL.Image

    from gateway.types import ToolRequest, Turn

    def grey() -> str:
        data = io.BytesIO()
        PIL.Image.new("RGB", (576, 1024), (128, 128, 128)).save(data, "PNG")
        return file_store.save("proto_fake.png", data.getvalue())

    picture = grey()
    form = {
        "everything_shown": [
            {"name": "the product", "when": "at the start", "looks_from": "library item 1"}
        ],
        "camera": "A vertical phone video frame, handheld at chest height.",
        "person_and_room": "Hands only; a desk with a lamp, a mug and a notebook.",
        "pose": "Right hand holds it, front to the camera as photo 1 shows.",
        "inside": "not needed here",
        "size": "a quarter of the frame width",
        "moment": "mid-action",
        "light": "window light from the left; right side in soft shadow",
        "bodies": "Exactly two hands.",
        "anything_else": "not needed here",
    }
    script: dict[str, list[list[tuple[str, dict[str, Any]]]]] = {}

    def turns() -> list[list[tuple[str, dict[str, Any]]]]:
        return [
            [("look_at_library", {"item": 1, "video": None, "moment": None})],
            [("film", _film_form())],  # refused: no picture yet
            [("draw_starting_picture", form)],
            [("edit_starting_picture", {"keep": ["the hand", "the desk"], "change": "the logo"})],
            [("look_at_try", {"number": 1})],
            [("film", _film_form())],
            [("give_up", {"reason": "after the end"})],  # never reached
        ]

    def take_a_turn(*, session: Any, conversation: Any, **_: Any) -> Turn:
        left = script.setdefault(str(session.pk), turns())
        if not left:
            return Turn(says="done", calls=())
        calls = left.pop(0)
        return Turn(
            says="",
            calls=tuple(
                ToolRequest(call_id=f"c{len(conversation)}-{i}", tool=tool, arguments=arguments)
                for i, (tool, arguments) in enumerate(calls)
            ),
        )

    checked: dict[str, int] = {}

    def call_model(*, purpose: str, output: Any, handoff: Any, images: Any, **_: Any) -> Any:
        assert purpose == scene_agent.CHECK
        n = checked[handoff.product_name] = checked.get(handoff.product_name, 0) + 1
        passes = n > 1
        print(f"  check {handoff.product_name} #{n}: {len(images)} pictures, passes={passes}")
        return output(
            product_same={"passes": passes, "says": "logo wrong" if not passes else "same"},
            physics={"passes": True, "says": "fine"},
        )

    def edit_picture(*, prompt: str, pictures: list[str], **_: Any) -> str:
        print(f"  picture: {len(pictures)} sent; prompt: {prompt[:300]}...")
        return picture

    setattr(loop, "take_a_turn", take_a_turn)  # noqa: B010
    setattr(scene_agent, "call_model", call_model)  # noqa: B010
    setattr(scene_agent, "edit_picture", edit_picture)  # noqa: B010


def _film_form() -> dict[str, Any]:
    return {
        "shot_size": "close-up",
        "frame_shows": "Two hands at a desk by a window.",
        "product": "a black mouse",
        "camera": "Shake Slightly, natural handheld sway.",
        "beats": ["the hand slides it left", "then right", "it repeats"],
        "shows_up_later": "not needed here",
        "small_life": "not needed here",
        "lock_line": "The mouse keeps its exact shape, colour and printed words.",
        "anything_else": "not needed here",
    }


if __name__ == "__main__":
    main(sys.argv[1:])
