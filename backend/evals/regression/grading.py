"""Build the regression page with the grading-page skill's renderer and media layout."""

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .questions import julian_questions
from .store import Store, atomic_write_text, read_json, write_json

SKILL = Path(__file__).resolve().parents[3] / ".claude/skills/grading-page/scripts"


class Media:
    """Copy immutable full-resolution assets under the skill's portable hashed names."""

    def __init__(self, output: Path) -> None:
        self.output = output
        self.urls: dict[str, str] = {}
        self.reencoded: list[str] = []

    def keep(self, path: Path, key: str) -> str:
        if path.suffix == ".m4a":
            key = key.removesuffix(".m4a") + ".mp4"
        if key in self.urls:
            return key
        if not path.is_file():
            raise FileNotFoundError(f"Grading media is missing: {path}")
        suffix = ".mp4" if path.suffix == ".m4a" else path.suffix.lower()
        name = hashlib.sha1(key.encode()).hexdigest()[:14] + suffix
        target = self.output / "media" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.stat().st_size > 15_000_000 and path.suffix.lower() in (".mp4", ".mov", ".webm"):
            probe = subprocess.run(
                [
                    os.environ.get("FFPROBE", "ffprobe"),
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            seconds = float(probe.stdout)
            bitrate = max(128, int(13_000_000 * 8 / seconds / 1000) - 96)
            subprocess.run(
                [
                    os.environ.get("FFMPEG", "ffmpeg"),
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    str(path),
                    "-c:v",
                    "libx264",
                    "-preset",
                    "medium",
                    "-crf",
                    "20",
                    "-maxrate",
                    f"{bitrate}k",
                    "-bufsize",
                    f"{bitrate * 2}k",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "96k",
                    "-movflags",
                    "+faststart",
                    str(target),
                ],
                capture_output=True,
                check=True,
                timeout=600,
            )
            self.reencoded.append(key)
        else:
            shutil.copyfile(path, target)
        if target.stat().st_size > 15_000_000:
            raise ValueError(f"Grading asset {key} is over the Artifact's 15 MB file limit")
        self.urls[key] = f"media/{name}"
        return key

    def finish(self) -> None:
        total = sum((self.output / url).stat().st_size for url in self.urls.values())
        if total > 1024**3 or len(self.urls) > 5000:
            raise ValueError("Grading media exceeds the Artifact's 1 GiB / 5,000-file asset limit")
        write_json(self.output / "need.json", {"assets": sorted(self.urls), "files": []})
        shutil.copyfile(SKILL / "prepare_media.py", self.output / "prepare_media.py")
        subprocess.run(
            [sys.executable, str(self.output / "prepare_media.py")],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )


def _legacy_inputs(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return dict(line.split(": ", 1) for line in path.read_text().splitlines() if ": " in line)


def _scenario(store: Store, run: str, key: str, media: Media, side: str) -> dict[str, Any]:
    export = store.export_dir(run, key)
    base = export / key if (export / key).is_dir() else export
    meta = read_json(base / "regression.json")
    identity = meta.get("scenario", {})
    ad_meta = read_json(base / "ad.json")
    answers = store.read_answers(run, key)
    results = read_json(export / "results.json")
    first_reencoded = len(media.reencoded)

    def asset(relative: str) -> str:
        return media.keep(base / relative, f"{side}/{key}/{relative}")

    scenes = meta.get("scenes") or [
        {"number": int(folder.name.split("-")[1]), **_legacy_inputs(folder / "inputs.txt")}
        for folder in sorted(base.glob("scene-*"))
        if folder.is_dir() and folder.name.split("-")[-1].isdigit()
    ]
    out_scenes = []
    taps = [f"{key}__no-worse"]
    for scene in scenes:
        number = int(scene["number"])
        where = base / f"scene-{number}"
        result = results.get(f"{key}:{number}", {})
        kept = result.get("kept") or {}
        picture = scene.get("picture") or next(
            (str(p.relative_to(base)) for p in sorted(where.glob("start-picture.*"))), None
        )
        clips = []
        tries = result.get("tries") or [
            {"pk": n + 1, "remake": None if p.stem == "first" else int(p.stem.split("-")[1])}
            for n, p in enumerate(sorted(where.glob("*.mp4")))
        ]
        for i, clip in enumerate(tries):
            remake = clip.get("remake")
            filename = "first.mp4" if remake is None else f"remake-{remake}.mp4"
            relative = f"scene-{number}/{filename}"
            current = clip.get("pk") == kept.get("pk") if kept else relative == scene.get("clip")
            grade_id = f"{key}__s{number}__clip" if current else f"{key}__s{number}__take{i + 1}"
            taps.append(grade_id)
            ruling = clip.get("ruling") or {}
            file = asset(relative)
            clips.append(
                {
                    "pk": clip.get("pk", i + 1),
                    "broll": True,
                    "video": file,
                    "file": file,
                    "seconds": clip.get("seconds"),
                    "verdict": ruling.get("verdict"),
                    "inAd": current,
                    "checks": ruling.get("checks"),
                    "why": None,
                    "changed": None,
                    "reason": None,
                    "prev": None,
                    "newPic": False,
                    "sent": {"prompt": (clip.get("sent") or {}).get("prompt"), "video": {}},
                    "make": None,
                    "form": None,
                    "failures": [],
                    "ruling": ruling,
                    "line": "",
                    "gradeId": grade_id,
                }
            )
        start_pics: list[dict[str, Any]] = (
            [
                {
                    "file": asset(picture),
                    "label": "Kept starting picture",
                    "checks": [],
                    "status": None,
                    "ask": None,
                    "image": None,
                    "kept": True,
                }
            ]
            if picture
            else []
        )
        if picture:
            taps.append(f"{key}__s{number}__picture")
        out_scenes.append(
            {
                "n": number,
                "part": scene.get("part", ""),
                "kind": result.get("kind", "broll"),
                "kind_why": "",
                "broll": "",
                "line": scene.get("line", scene.get("LINE", result.get("line", ""))),
                "shows": scene.get("shows", scene.get("SHOWS", result.get("shows", ""))),
                "gave": bool(result.get("gave_up")),
                "gaves": [result["gave_up"]] if result.get("gave_up") else [],
                "run": None,
                "questions": [],
                "at": None,
                "startPics": start_pics,
                "picture": start_pics[0]["file"] if start_pics else None,
                "picked": [],
                "clips": clips,
                "agent": [],
            }
        )
    if out_scenes:
        taps.append(f"{key}__plan")
    finished_ad = next(
        (asset(name) for name in ("ad-latest.mp4", "ad.mp4") if (base / name).exists()), None
    )
    if finished_ad:
        taps.append(f"{key}__ad")
    page_files = meta.get("page_files", [])
    page_text = "\n\n".join(
        (base / name).read_text() for name in page_files if Path(name).suffix in (".txt", ".md")
    )
    if page_files:
        taps.append(f"{key}__page")
    notes = []
    for number, note in enumerate(meta.get("notes", []), 1):
        if not note.get("after"):
            continue
        n = note.get("number", number)
        notes.append(
            {
                "number": n,
                "text": note.get("text", ""),
                "before": asset(note["before"]) if note.get("before") else None,
                "after": asset(note["after"]),
            }
        )
        taps.append(f"{key}__note{n}")
    photos = []
    question = answers.get("answers", {}).get("page_photos_this_product", {})
    labels = store.read_photo_labels().get(key, {})
    for item in question.get("items", []):
        if item.get("verdict") != "needs_label":
            continue
        if item["item"].removeprefix("photo-") in labels:
            continue
        file = item.get("file")
        if not file:
            raise ValueError(f"{key}: unlabeled photo {item['item']} has no saved file")
        photo_key = media.keep(store.media_dir(run, key) / file, f"{side}/{key}/photo/{file}")
        photos.append({"id": f"{key}__{item['item']}", "file": photo_key})
        taps.append(f"{key}__{item['item']}")
    ad: dict[str, Any] = {
        "key": key,
        "run": run,
        "kind": identity.get("kind", "full" if finished_ad else "picture"),
        "cost": answers.get("cost_usd", 0),
        "url": identity.get("link", ad_meta.get("product_url", "")),
        "trace": None,
        "session": "",
        "app": None,
        "final": "",
        "ad": finished_ad,
        "adNote": None,
        "reds": [],
        "gaveup": [],
        "watch": [],
        "answers": [],
        "scenes": out_scenes,
        "stats": {"reds": 0, "gaveup": 0, "pass": 0, "fail": 0, "unsure": 0},
        "r7": None,
        "shared": [],
        "library": [],
        "videos": [],
        "costs": [],
        "warnings": [],
        "chat": [],
        "producer": [],
        "product": "",
        "colour": "",
        "size": "",
        "behaviour": "",
        "pageText": page_text,
        "hasPage": bool(page_files),
        "notes": notes,
        "photos": photos,
        "tapIds": taps,
        "code": answers.get("answers", {}),
        "grades": store.read_grades(run),
    }
    if rich := read_json(base / "grading-records.json"):
        from .grading_adapter import enrich

        def raw_asset(file: str) -> str:
            return media.keep(store.media_dir(run, key) / file, f"{side}/{key}/raw/{file}")

        enrich(ad, rich, raw_asset)
    for scene in ad["scenes"]:
        # Clip archive numbers are at most the clip count. Continue that numeric range
        # for picture versions so case filing can keep its existing archive-doc pattern.
        archive_number = len(scene["clips"])
        for picture in scene["startPics"]:
            if not picture.get("kept"):
                archive_number += 1
                picture["gradeId"] = f"{key}__s{scene['n']}__take{archive_number}"
                taps.append(picture["gradeId"])
    ad["reencoded"] = len(media.reencoded) - first_reencoded
    return ad


def build_page(
    store: Store,
    run: str,
    *,
    output: Path | None = None,
    media_urls: Mapping[str, str] | None = None,
) -> Path:
    """Build using saved files only; no app, model call or network is needed."""
    record = store.read_run(run)
    if not record:
        raise ValueError(f"No saved run {run!r}")
    target = output or store.run_dir(run) / "grading-page"
    target.mkdir(parents=True, exist_ok=True)
    media = Media(target)
    ads = {}
    baselines = store.read_baselines()
    for key in record.get("scenarios", []):
        ad = _scenario(store, run, key, media, "new")
        old = baselines.get(key)
        ad["old"] = _scenario(store, old, key, media, "old") if old and old != run else None
        ads[key] = ad
    media.finish()
    data = {
        "run": run,
        "storageKey": f"regression-{run}",
        "media": {**media.urls, **(media_urls or {})},
        "ads": ads,
        "grades": store.read_grades(run),
        "questions": {
            stage: [
                q.words.split(" (from his past complaints")[0].split(" (existing case file")[0]
                for q in julian_questions(stage)
            ]
            for stage in ("page", "plan", "pictures", "clips", "ad", "note")
        },
    }
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    # Keep the skill's renderer and autosave code. Its old round-specific shell is replaced
    # by the regression extension; its rendering helpers continue to have one source.
    renderer = (SKILL / "page.js").read_text().split("/* shell */")[0]
    renderer = renderer.replace('"r8-drafts"', 'DATA.storageKey+"-drafts"').replace(
        '"r8-view"', 'DATA.storageKey+"-view"'
    )
    renderer = renderer.replace(
        "AI summary of the judge's record. Not a grade.", "Summary from app records, not a grade."
    ).replace(
        "AI summary of the two prompts. Not a grade.", "Changes recorded by the app, not a grade."
    )
    extension = Path(__file__).with_name("grading.js").read_text()
    page = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(run)} regression grading</title>
<style>{(SKILL / "page.css").read_text()}</style></head><body>
<div class="shell"><nav class="rail" id="rail" aria-label="Scenarios"></nav>
<main id="view"></main></div>
<script type="application/json" id="data">{blob}</script>
<script>{renderer}\n{extension}</script></body></html>"""
    path = target / "grading.html"
    atomic_write_text(path, page)
    write_json(target / "media-map.json", media.urls)
    write_json(
        target / "publish.json",
        {
            "file_path": "grading.html",
            "capabilities": {"db": {}, "assets": {}},
            "privacy": "private",
            "asset_batches": "abatches.json",
            "asset_names": "aname.json",
            "reencoded": media.reencoded,
        },
    )
    return path


def import_grades(store: Store, run: str, source: Path) -> int:
    """File an exported Artifact grades collection, keeping taps and words verbatim."""
    record = store.read_run(run)
    if not record:
        raise ValueError(f"No saved run {run!r}")
    if source.is_dir():
        folder = source / "grades" if (source / "grades").is_dir() else source
        raw = {path.stem: json.loads(path.read_text()) for path in sorted(folder.glob("*.json"))}
    else:
        raw = json.loads(source.read_text())
    if isinstance(raw, list):
        raw = {doc["id"]: doc.get("data", doc) for doc in raw}
    if isinstance(raw, dict) and "grades" in raw:
        raw = raw["grades"]
    if not isinstance(raw, dict):
        raise ValueError("Grades must be a collection of document ids and grade/note/at records")
    grades = store.read_grades(run)
    labels = store.read_photo_labels()
    accepted = {}
    for doc_id, wrapped in raw.items():
        scenario, separator, suffix = doc_id.partition("__")
        if not separator or scenario not in record.get("scenarios", []):
            if doc_id != "round__notes":
                raise ValueError(f"Grade {doc_id!r} does not belong to run {run!r}")
        doc = wrapped.get("data", wrapped) if isinstance(wrapped, dict) else None
        if (
            not isinstance(doc, dict)
            or set(doc) != {"grade", "note", "at"}
            or doc["grade"] not in (None, "pass", "fail")
            or not isinstance(doc["note"], str)
            or not isinstance(doc["at"], str)
        ):
            raise ValueError(f"Grade {doc_id!r}: expected grade (pass/fail/null), note and at")
        accepted[doc_id] = doc
        if suffix.startswith("photo-"):
            digest = suffix.removeprefix("photo-")
            if not re.fullmatch("[0-9a-f]{64}", digest):
                raise ValueError(f"Grade {doc_id!r}: photo needs its SHA-256 hash")
            if doc["grade"] is not None:
                labels.setdefault(scenario, {})[digest] = (
                    "this" if doc["grade"] == "pass" else "not"
                )
    grades.update(accepted)
    lines = [f"# {run}: Julian's grades", ""]
    for doc_id, doc in sorted(grades.items()):
        lines.extend([f"## {doc_id} [{doc['grade'] or 'no tap'}]", doc["note"], ""])
    store.write_grades(run, grades)
    store.write_grades_markdown(run, "\n".join(lines))
    store.write_photo_labels(labels)
    return len(accepted)


def main(argv: Sequence[str], *, store: Store) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run")
    parser.add_argument("--import-grades", type=Path, help="File the exported grades collection")
    parser.add_argument("--media-map", type=Path, help="Uploaded asset URLs, keyed by media key")
    parser.add_argument("--output", type=Path, help="Page output folder (default: in the run)")
    args = parser.parse_args(argv)
    try:
        if args.import_grades is not None:
            count = import_grades(store, args.run, args.import_grades)
            print(f"{args.run}: saved {count} grade documents and photo labels")
        else:
            mapping = json.loads(args.media_map.read_text()) if args.media_map else None
            if mapping is not None and (
                not isinstance(mapping, dict)
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items())
            ):
                raise ValueError("The media map must be media keys and their uploaded URLs")
            print(build_page(store, args.run, output=args.output, media_urls=mapping))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0
