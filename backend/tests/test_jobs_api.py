import pytest
from pytest_django import Settings
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from jobs.models import Job

from .conftest import MUG_FRONT, MUG_SIDE, served

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)


def test_a_job_made_in_the_chat_can_be_fetched_with_its_photos_and_scenes(
    api: APIClient,
    httpserver: HTTPServer,
    planned: None,
    product_page_url: str,
    settings: Settings,
) -> None:
    job = Job.objects.get()

    fetched = api.get(f"/api/jobs/{job.pk}/").json()

    # Progress and questions are told in the chat, so the job has neither.
    assert sorted(fetched) == [
        "created_at",
        "id",
        "photos",
        "product_url",
        "scenes",
        "status",
        "target_seconds",
    ]
    assert (
        fetched["id"],
        fetched["product_url"],
        fetched["target_seconds"],
        fetched["status"],
    ) == (
        str(job.pk),
        product_page_url,
        None,
        "planned",
    )
    # Each photo is the page's, and its link hands the browser the photo itself.
    assert [
        (photo["position"], photo["source_url"], served(photo["url"], settings))
        for photo in fetched["photos"]
    ] == [
        (1, httpserver.url_for("/cdn/mug-front.png"), MUG_FRONT),
        (2, httpserver.url_for("/cdn/mug-side.png"), MUG_SIDE),
    ]
    assert [(scene["number"], scene["line"]) for scene in fetched["scenes"]] == [
        (1, "Meet the Stoneware Mug from Kiln & Co."),
        (2, "Hand-thrown, holds 350 ml, and dishwasher safe."),
        (3, "Yours for $24.00."),
    ]
