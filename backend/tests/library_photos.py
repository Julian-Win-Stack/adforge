"""Small page-photo fixtures for chat flow tests."""

from adforge import file_store
from jobs.library import PagePhoto, PictureLabel, add_to_library
from jobs.models import Job, ProductPhoto

from .conftest import photo


def label(kind: str, side: str, colour: str, words: str, alone: str = "yes") -> PictureLabel:
    return PictureLabel.model_validate(
        {"kind": kind, "side": side, "alone": alone, "colour": colour, "label": words}
    )


def page_photo(job: Job, picture: PictureLabel, *, colour_photo: bool = False) -> int:
    """`picture` as the job's next page photo, in its library: gives its library number."""
    position = job.photos.count() + 1
    file = file_store.save(f"jobs/{job.pk}/photo-{position}.png", photo(position))
    ProductPhoto.objects.create(
        job=job,
        position=position,
        file=file,
        source_url=f"https://shop.example/{position}.png",
        shows_product_colour=colour_photo,
    )
    return add_to_library(job, file, picture, PagePhoto(photo=position)).number
