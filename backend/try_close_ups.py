"""TRY (throwaway): replay round 12 starting-picture tries with and without close-ups.

uv run python try_close_ups.py <sample.json> <media dir> <out.json> [--free] [--only N]

Arm A: the try's recorded prompt and pictures. Arm B: the same plus a close-up after each
picked photo, image numbers shifted. Both judged by today's picture check, given the
photos the app would give it (arm B: plus the close-ups)."""

import json
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")
import django  # noqa: E402

django.setup()

from django.db import connection  # noqa: E402
from django.db.models import Sum  # noqa: E402

from adforge import file_store  # noqa: E402
from agents.scene_agent import close_ups  # noqa: E402
from agents.scene_agent.photos import SelectedPhoto  # noqa: E402
from agents.scene_agent.picture_check import (  # noqa: E402
    FirstSceneAnswers,
    StartingPictureAnswers,
    StartingSceneHandoff,
    instructions,
    verdict,
)
from agents.scene_forms import _box  # noqa: E402
from gateway.gateway import call_model, edit_picture  # noqa: E402
from gateway.models import ModelCall  # noqa: E402
from gateway.types import Image  # noqa: E402

sample_path, media_dir, out_path = sys.argv[1:4]
FREE = "--free" in sys.argv
ONLY = int(sys.argv[sys.argv.index("--only") + 1]) if "--only" in sys.argv else None
CAP = 11.5
lock = threading.Lock()
media = {p.stem: p for p in Path(media_dir).iterdir()}
keys: dict[str, str] = {}


def key(mid: str) -> str:
    with lock:
        if mid not in keys:
            path = media[mid]
            name = f"try/{mid}{path.suffix}"
            keys[mid] = (
                name if file_store.exists(name) else file_store.save(name, path.read_bytes())
            )
        return keys[mid]


def spent() -> float:
    total = ModelCall.objects.filter(purpose="draw_scene_picture").aggregate(s=Sum("cost_usd"))
    return float(total["s"] or 0)


def renumber(text: str, mapping: dict[int, int]) -> str:
    return re.sub(
        r"\b([Ii]mage) (\d+)\b",
        lambda m: f"{m[1]} {mapping.get(int(m[2]), int(m[2]))}",
        text,
    )


def arms(t: dict) -> dict:
    first = 2 if t["editing"] else 1
    refs_mid = t["shown"][1:] if t["editing"] else t["shown"]
    n_picks = len(t["picks"])
    if t["editing"]:
        lines = [f"Image {n}: {take.strip()}" for n, take in enumerate(t["picks"], 2)]
        lines += [
            f"Image {n}: the product's packaging; draw its box as this photo shows it."
            for n in range(len(lines) + 2, len(refs_mid) + 2)
        ]
    else:
        lines = list(t["check_handoff"]["photos"])
        assert len(lines) == len(refs_mid), (t["pk"], lines, refs_mid)
    refs = [
        SelectedPhoto(file=key(mid), line=line, library_number=None)
        for mid, line in zip(refs_mid, lines, strict=True)
    ]
    old_block = " ".join(filter(None, map(_box, lines)))
    assert old_block in t["prompt"], (t["pk"], old_block[:200], t["prompt"][:400])
    with_b = close_ups.with_close_ups(
        None, refs, n_picks, t["check_handoff"]["product_name"], first
    )
    # Old image number -> new, for the words outside the job lines (the form's boxes).
    mapping = {1: 1} if t["editing"] else {}
    new_numbers = [n for n, p in enumerate(with_b, first) if not p.close_up]
    mapping.update({old: new for old, new in enumerate(new_numbers, first)})
    before, after = t["prompt"].split(old_block, 1)
    new_block = " ".join(filter(None, map(_box, [p.line for p in with_b])))
    prompt_b = renumber(before, mapping) + new_block + renumber(after, mapping)
    lead = [key(t["shown"][0])] if t["editing"] else []
    # The judge: the try's recorded evidence (arm A); plus each close-up after its photo (B).
    evidence = [
        SelectedPhoto(file=key(mid), line=line)
        for mid, line in zip(t["check_shown"][1:], t["check_handoff"]["photos"], strict=True)
    ]
    added = {with_b[j - 1].file: p for j, p in enumerate(with_b) if p.close_up}
    judged_b: list[SelectedPhoto] = []
    for photo in evidence:
        judged_b.append(photo)
        if photo.file in added:
            judged_b.append(added[photo.file])
    judged_b = [
        p.model_copy(update={"line": re.sub(r"^Image \d+\b", f"Image {n}", p.line)})
        for n, p in enumerate(judged_b, 1)
    ]
    for n, p in enumerate(judged_b, 1):
        if p.close_up:
            prev = judged_b[n - 2].line.split(":")[0].split(" ")[1]
            judged_b[n - 1] = p.model_copy(
                update={
                    "line": re.sub(r"close-up of image \d+", f"close-up of image {prev}", p.line)
                }
            )
    return {
        "A": {"prompt": t["prompt"], "pictures": lead + [p.file for p in refs], "judge": evidence},
        "B": {"prompt": prompt_b, "pictures": lead + [p.file for p in with_b], "judge": judged_b},
        "close_ups": [p.file for p in with_b if p.close_up],
    }


def judge(t: dict, picture: str, photos: list[SelectedPhoto]) -> dict:
    h = t["check_handoff"]
    first = bool(h["first_scene"])
    answer = call_model(
        job=None,
        purpose="check_scene_picture",
        instructions=instructions(True, first),
        handoff=StartingSceneHandoff(**{**h, "photos": [p.line for p in photos]}),
        output=FirstSceneAnswers if first else StartingPictureAnswers,
        pay_once=True,
        images=[
            Image(label="The starting picture to check:", key=picture),
            *(Image(label=p.line, key=p.file) for p in photos),
        ],
    )
    return {"status": "passed" if verdict(answer) else "failed", "answers": answer.model_dump()}


def run(t: dict) -> dict:
    try:
        plan = arms(t)
        row = {k: t[k] for k in ("ad", "pk", "tid", "editing", "kind", "bad", "ruling_status")}
        row["original"] = key(t["made"])
        row["close_ups"] = plan["close_ups"]
        for arm in ("A", "B"):
            a = plan[arm]
            row[arm] = {
                "prompt": a["prompt"],
                "pictures": a["pictures"],
                "judge_photos": [p.model_dump(exclude_none=True) for p in a["judge"]],
            }
            if FREE:
                continue
            if spent() > CAP:
                row[arm]["skipped"] = "cap reached"
                continue
            picture = edit_picture(
                job=None, purpose="draw_scene_picture", prompt=a["prompt"], pictures=a["pictures"]
            )
            row[arm]["picture"] = picture
            row[arm]["ruling"] = judge(t, picture, a["judge"])
        return row
    except Exception as error:  # noqa: BLE001
        return {"ad": t["ad"], "pk": t["pk"], "error": repr(error)[:500]}
    finally:
        connection.close()


sample = json.load(open(sample_path))
tries = [{**t, "group": "failed"} for t in sample["fails"]] + [
    {**t, "group": "passed"} for t in sample["passes"]
]
if ONLY:
    tries = tries[:ONLY]
done = {}
if Path(out_path).exists():
    done = {
        r["pk"]: r
        for r in json.load(open(out_path))
        if "error" not in r and "picture" in r.get("B", {})
    }
todo = [t for t in tries if t["pk"] not in done]
print(f"{len(todo)} to run, {len(done)} done, spent so far ${spent():.2f}", flush=True)
results = list(done.values())
with ThreadPoolExecutor(6 if FREE else 4) as ex:
    for row in ex.map(run, todo):
        results.append(row)
        print(
            row["pk"],
            row.get("error") or {a: row.get(a, {}).get("ruling", {}).get("status") for a in "AB"},
            f"${spent():.2f}",
            flush=True,
        )
        json.dump(results, open(out_path, "w"), indent=1)
print("spent", spent())
