"""The grading-page skill builder's image/video record adapters, without round names."""

from collections.abc import Callable
from typing import Any


def _image(picture: dict[str, Any], asset: Callable[[str], str]) -> dict[str, Any] | None:
    image = picture.get("image")
    if not image:
        return None
    labels = {p["file"]: p.get("line", "") for p in (picture.get("sent") or {}).get("photos", [])}
    return {
        "model": image.get("model"),
        "provider": image.get("provider"),
        "cost": image.get("cost"),
        "prompt": image.get("prompt"),
        "inputs": [
            {"file": asset(key), "label": f"{number}. {labels.get(key) or 'no line recorded'}"}
            for number, key in enumerate(image.get("pictures", []), 1)
        ],
    }


def _video(clip: dict[str, Any], asset: Callable[[str], str]) -> dict[str, Any]:
    """Only the film model's own handoff is evidence that a picture went to H3."""
    made = clip.get("make") or {}
    handoff = made.get("handoff") or {}
    sent = clip.get("sent") or {}
    pictures = []
    for field, label in (("starting_picture", "Starting picture"), ("end_picture", "End picture")):
        key = handoff.get(field)
        if key:
            pictures.append({"file": asset(key), "label": label})
    for number, key in enumerate(handoff.get("example_pictures") or [], 1):
        pictures.append({"file": asset(key), "label": f"Example picture {number}"})
    references = [
        {
            "file": asset(ref["file"]),
            "video": ref.get("position"),
            "start": ref.get("start"),
            "end": ref.get("end"),
            "trim": ref.get("trim"),
        }
        for ref in handoff.get("reference_videos") or []
        if ref.get("file")
    ]
    # An old export may have only its saved sent list. Keep it visibly apart from the
    # model's own record rather than claiming its source photos went to the video model.
    return {
        "recorded": bool(handoff),
        "mode": "reference"
        if handoff.get("example_pictures")
        else "start_frame"
        if handoff.get("starting_picture")
        else sent.get("mode"),
        "model": made.get("model"),
        "provider": made.get("provider"),
        "cost": made.get("cost"),
        "seconds": handoff.get("seconds"),
        "enhancement": handoff.get("prompt_enhancement"),
        "prompt": handoff.get("prompt", sent.get("prompt")),
        "pictures": pictures,
        "refs": references,
    }


def _tools(records: list[dict[str, Any]], asset: Callable[[str], str]) -> list[dict[str, Any]]:
    return [
        {
            "tool": t["tool"],
            "head": t.get("fold") or "",
            "cost": t.get("cost"),
            "said": t.get("said"),
            "why": t.get("why"),
            "args": t.get("args"),
            "result": t.get("result"),
            "bad": t.get("answer") == "cant",
            "pics": [
                {"key": asset(p["key"]), "label": p.get("label")}
                for p in t.get("pictures") or []
                if p.get("key")
            ],
        }
        for t in records
    ]


def enrich(ad: dict[str, Any], rich: dict[str, Any], asset: Callable[[str], str]) -> None:
    """Add the full records behind the simple regression export to the skill's UI shape."""
    by_number = {s["number"]: s for s in rich.get("scenes", [])}
    stats = ad["stats"]
    for scene in ad["scenes"]:
        source = by_number.get(scene["n"], {})
        if not source:
            continue
        scene.update(
            {
                "part": source.get("part", ""),
                "kind": source.get("kind", scene["kind"]),
                "kind_why": source.get("kind_why", ""),
                "broll": source.get("broll_kind", ""),
            }
        )
        current_picture = source.get("current_picture")
        if current_picture is None:
            current_clip: dict[str, Any] = next(
                (clip for clip in source.get("clips", []) if clip["pk"] == source.get("current")),
                {},
            )
            current_picture = (current_clip.get("picture") or {}).get("pk")
        pictures = []
        for picture in source.get("pictures", []):
            ruling = picture.get("ruling") or {}
            pictures.append(
                {
                    "file": asset(picture["file"]),
                    "label": (
                        "End picture" if picture["kind"] == "end_picture" else "Starting picture"
                    )
                    + f" v{picture['version']}",
                    "checks": [
                        {
                            "name": key.replace("_", " "),
                            "pass": bool(answer.get("passes")),
                            "says": answer.get("says", ""),
                        }
                        for key, answer in (ruling.get("answers") or {}).items()
                    ],
                    "status": ruling.get("status"),
                    "ask": picture.get("sent"),
                    "image": _image(picture, asset),
                    "kept": picture["pk"] == current_picture,
                }
            )
        scene["startPics"] = pictures
        previous = None
        for clip in scene["clips"]:
            raw: dict[str, Any] = next(
                (c for c in source.get("clips", []) if c["pk"] == clip["pk"]), {}
            )
            if not raw:
                continue
            video = _video(raw, asset)
            sent = raw.get("sent") or {}
            ruling = raw.get("ruling") or {}
            failures = [
                {
                    "check": f.get("check"),
                    "problems": f.get("problems"),
                    "fault": (f.get("answer") or {}).get("fault"),
                    "times": (f.get("answer") or {}).get("fault_frame_times"),
                }
                for f in ruling.get("failures") or []
            ]
            problems = [str(p) for f in failures for p in f.get("problems") or []]
            film: dict[str, Any] = next(
                (
                    t
                    for t in reversed(source.get("agent", []))
                    if t.get("tool") == "film" and t.get("at", "") <= raw.get("created_at", "")
                ),
                {},
            )
            clip.update(
                {
                    "sent": {
                        "video": video,
                        "prompt": video.get("prompt"),
                        "colour": sent.get("colour"),
                        "recolours": sent.get("recolours"),
                        "left_out": sent.get("left_out"),
                        "strangers": sent.get("strangers_left_out"),
                    },
                    "make": raw.get("make"),
                    "why": " ".join(problems) or None,
                    "reason": film.get("why") or (film.get("args") or {}).get("reason"),
                    "prev": (previous.get("sent") or {}).get("prompt") if previous else None,
                    "newPic": bool(previous and previous.get("picture") != raw.get("picture")),
                    "failures": failures,
                    "form": (film.get("args") or {}).get("form"),
                    "line": raw.get("motion_prompt") or raw.get("text", ""),
                }
            )
            previous = raw
            if clip["verdict"] in stats:
                stats[clip["verdict"]] += 1
        scene["agent"] = _tools(source.get("agent", []), asset)
        scene["picked"] = [
            {"file": asset(p["key"]), "label": p.get("label", "")}
            for t in source.get("agent", [])
            if t.get("tool") == "look_at_library"
            for p in t.get("pictures") or []
            if p.get("key")
        ]
        runs = [run for fix in source.get("fixes", []) for run in fix.get("runs", [])]
        scene["run"] = runs[0] if runs else None
    ad.update(
        {
            "session": rich.get("session", ""),
            "trace": rich.get("trace"),
            "product": rich.get("product", ""),
            "colour": rich.get("product_colour", ""),
            "size": rich.get("product_size", ""),
            "behaviour": rich.get("behaviour", ""),
            "pageText": rich.get("page_text", ad["pageText"]),
            "shared": [
                {**p, "file": asset(p["file"])}
                for p in rich.get("shared", [])
                if p.get("file")
                and p["kind"]
                in ("portrait", "before_picture", "person_in_room", "room_alone", "music")
            ],
            "library": [
                {**p, "file": asset(p["file"])} for p in rich.get("library", []) if p.get("file")
            ],
            "videos": [
                {
                    **v,
                    "url": v.get("source_url", ""),
                    "file": asset(v["file"]) if v.get("file") else None,
                }
                for v in rich.get("videos", [])
            ],
            "costs": sorted(rich.get("costs", {}).items(), key=lambda pair: -pair[1]),
            "warnings": rich.get("warnings", []),
            "chat": rich.get("messages", []),
            "producer": _tools(rich.get("producer", []), asset),
        }
    )
    ad["reds"] = [w for w in ad["warnings"] if w.get("level") == "problem"]
    stats["reds"] = len(ad["reds"])
    ad["answers"] = [
        {
            "ad": ad["key"],
            "a": message.get("text", ""),
            "q": next(
                (
                    m.get("text", "")
                    for m in reversed(ad["chat"][:number])
                    if m.get("role") != "user"
                ),
                "",
            ),
        }
        for number, message in enumerate(ad["chat"])
        if number and message.get("role") == "user"
    ]
