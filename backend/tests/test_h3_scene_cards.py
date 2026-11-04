"""The browser reads scene-agent talking pictures, tries and both clip checks on one card."""

from typing import Any

import pytest
from rest_framework.test import APIClient

from adforge import file_store
from chat.models import Session
from jobs.models import Job, ProducedItem, Scene

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("historical", "judge", "words", "verdict", "problem"),
    [
        (True, "pass", "pass", "pass", ""),
        (True, "pass", "fail", "fail", "The spoken price differs."),
        (True, "pass", "unchecked", "unsure", ""),
        (True, "unsure", "pass", "unsure", ""),
        (True, "fail", "pass", "fail", "The product changed shape."),
        (True, "fail", "fail", "fail", "The product changed shape."),
        (True, "pass", None, "pass", ""),
        (False, None, None, "not_checked", ""),
    ],
)
def test_talking_card_shows_its_picture_and_every_try_with_both_check_verdicts(
    api: APIClient,
    historical: bool,
    judge: str | None,
    words: str | None,
    verdict: str,
    problem: str,
) -> None:
    session = Session.objects.create()
    job = Job.objects.create(session=session)
    scene = Scene.objects.create(
        job=job, number=1, kind=Scene.Kind.TALKING, line="Only ten dollars."
    )
    picture = ProducedItem.objects.create(
        job=job, scene=scene, kind=ProducedItem.Kind.STARTING_PICTURE, file="first-frame.png"
    )
    ruling: dict[str, Any] = {}
    if judge is not None:
        ruling = {
            "verdict": judge,
            "checks": {"product": judge},
            "answers": {"product": [{"verdict": judge, "problem": "The product changed shape."}]},
        }
    if words is not None:
        ruling["word_check"] = {
            "line": scene.line,
            "said": "Only twenty dollars." if words == "fail" else scene.line,
            "verdict": words,
            "reason": "The spoken price differs." if words == "fail" else "No difference.",
            "settled_by": "model",
        }
    for number in (1, 2):
        ProducedItem.objects.create(
            job=job,
            scene=scene,
            kind=ProducedItem.Kind.CLIP,
            version=number,
            file=f"try-{number}.mp4",
            picture=picture,
            ruling=ruling,
        )

    response = api.get(f"/api/sessions/{session.pk}/scenes/")

    assert response.status_code == 200
    (card,) = response.json()
    assert card["picture"] == file_store.url(picture.file)
    assert [version["number"] for version in card["versions"]] == [1, 2]
    assert [(version["verdict"], version["problem"]) for version in card["versions"]] == [
        (verdict, problem),
        (verdict, problem),
    ]
    assert all(version["picture"] == file_store.url(picture.file) for version in card["versions"])


def test_a_marked_talking_card_shows_its_starting_picture_before_any_clip(api: APIClient) -> None:
    session = Session.objects.create()
    job = Job.objects.create(session=session)
    scene = Scene.objects.create(
        job=job, number=1, kind=Scene.Kind.TALKING, line="Only ten dollars."
    )
    picture = ProducedItem.objects.create(
        job=job, scene=scene, kind=ProducedItem.Kind.STARTING_PICTURE, file="first-frame.png"
    )

    response = api.get(f"/api/sessions/{session.pk}/scenes/")

    assert response.status_code == 200
    assert response.json() == [
        {"scene": 1, "picture": file_store.url(picture.file), "versions": []}
    ]
