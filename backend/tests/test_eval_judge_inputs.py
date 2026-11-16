"""The judge receives saved media and product facts, never the app's judgements."""

from pathlib import Path

from evals.judge.inputs import inputs_for_run_item
from evals.regression.items import graded_items
from evals.regression.store import Store, write_json


def test_saved_judge_inputs_keep_only_the_product_and_item_context(tmp_path: Path) -> None:
    store = Store(tmp_path)
    base = store.export_dir("baseline-2", "mug") / "mug"
    base.mkdir(parents=True)
    names = ["page-photo-3.png", "page-photo-8.png", "s1.png", "s1.mp4", "ad.mp4"]
    for name in names:
        (base / name).write_bytes(b"saved media")
    (base / "page.txt").write_text("The real product's page.")
    write_json(
        base / "regression.json",
        {
            "scenario": {"key": "mug"},
            "page_files": ["page.txt", *names[:2]],
            "scenes": [
                {
                    "number": 1,
                    "line": "Pour your coffee.",
                    "shows": "Coffee poured into the mug.",
                    "picture": "s1.png",
                    "clip": "s1.mp4",
                }
            ],
            "page_read": {"notes": ["APP VERDICT"]},
        },
    )
    write_json(
        base / "grading-records.json",
        {
            "product": "Stoneware mug",
            "page_text": "The real product's page.",
            "library": [
                {"number": 8, "file": "raw/side.png", "label": "Its handle side."},
                {"number": 3, "file": "raw/front.png", "label": "Its front."},
            ],
            "warnings": ["APP VERDICT"],
            "producer": [{"prompt": "APP PROMPT"}],
        },
    )

    items = graded_items(store, "baseline-2", "mug")
    picture = next(item for item in items if item.stage == "pictures")
    inputs = inputs_for_run_item(store, "baseline-2", "mug", picture)

    assert inputs.files == (base / "s1.png",)
    assert inputs.page_photos == (base / names[0], base / names[1])
    assert inputs.case_handoff() == {
        "stage": "pictures",
        "item": "s1",
        "line": "Pour your coffee.",
        "shows": "Coffee poured into the mug.",
        "product_name": "Stoneware mug",
        "product_text": "The real product's page.",
        "page_photo_labels": ["Its front.", "Its handle side."],
    }
    assert inputs.case_files() == (base / "s1.png", base / names[0], base / names[1])
    ad = next(item for item in items if item.stage == "ad")
    ad_inputs = inputs_for_run_item(store, "baseline-2", "mug", ad)
    assert ad_inputs.files == (base / "s1.mp4",)


def test_an_older_export_reads_its_saved_page_files_without_a_live_job(tmp_path: Path) -> None:
    store = Store(tmp_path)
    base = store.export_dir("baseline-2", "mug") / "mug"
    (base / "scene-1").mkdir(parents=True)
    (base / "scene-1/start-picture.png").write_bytes(b"picture")
    (base / "scene-1/inputs.txt").write_text("LINE: Lift it.\nSHOWS: A mug in her hand.\n")
    (base / "page-photo-2.png").write_bytes(b"photo")
    (base / "page.txt").write_text("A stoneware mug.")

    picture = next(i for i in graded_items(store, "baseline-2", "mug") if i.stage == "pictures")
    inputs = inputs_for_run_item(store, "baseline-2", "mug", picture)

    assert inputs.line == "Lift it."
    assert inputs.product_text == "A stoneware mug."
    assert inputs.page_photos == (base / "page-photo-2.png",)
    assert inputs.page_photo_labels == ("",)
