"""The producer: the agent the user talks to. What it is told to do, and its tools."""

from pydantic import Field

from jobs import page
from jobs.models import Job
from jobs.tasks import check_page, keep_page, save_photos

from .loop import Agent, Tool
from .models import ToolCall

INSTRUCTIONS = """\
You are the producer at AdForge. You make short vertical video ads for one product at a \
time, in a chat with the shop owner who sells it. In each ad a person speaks to camera, \
one line per scene.
You work by calling tools. Call one when you need it, read what it hands back, and decide \
what to do next. Before a tool that takes a while, say in one short sentence what you're \
about to do. When there is nothing left to do, or you need the shop owner, reply to them.
Every fact about the product comes from its page or from the shop owner. You have no tool \
that searches the web, so never look anything up, infer or guess. When something you need \
is missing or unclear, ask one short, specific question. When you can't do something, say \
so plainly and say why.
When a tool skips a product photo, tell the shop owner which photo and why. When a tool \
fails or refuses, tell the shop owner honestly what happened.
Keep your messages short and friendly, written for someone who isn't technical."""


class ReadPage(Tool):
    """Start a job for an ad, from the link to the product's page. Reads the page and keeps
    its text and its product photos: everything the ad says and shows comes from them. Says
    which photos were skipped and why."""

    name = "read_page"

    link: str = Field(description="The link to the product's page, as the shop owner gave it.")
    target_seconds: int | None = Field(
        description="How long the shop owner wants the ad to be, in seconds, or null if they "
        "didn't say."
    )

    def run(self, call: ToolCall) -> str:
        job = Job.objects.create(
            session=call.session, product_url=self.link, target_seconds=self.target_seconds
        )
        call.job = job
        call.save(update_fields=["job"])
        try:
            download = page.download(self.link, max_bytes=page.MAX_PAGE_BYTES, what="product page")
        except page.PageUnreadable as error:
            return (
                f"The page couldn't be read: {error} Ask the shop owner for a working link to "
                "the product's own page."
            )
        product_page = keep_page(job, download)
        check = check_page(job, download, product_page)
        if check.decision == "unreadable":
            return (
                f"The page was read but can't be used: {check.reason} Ask the shop owner for "
                "a link to the product's own page."
            )
        skipped = save_photos(job, product_page.photo_urls)
        kept = job.photos.count()
        told = [f"Started job {job.pk} and read {download.final_url}. {check.reason}"]
        if download.final_url != self.link:
            told.append(f"The link led to {download.final_url}, so that is the page read.")
        told.append(f"Kept {kept} product photo{'s' if kept != 1 else ''}.")
        if skipped:
            told.append(f"Skipped {len(skipped)} photo{'s' if len(skipped) != 1 else ''}:")
            told += [f"- {photo.url}: {photo.reason}" for photo in skipped]
        if kept == 0:
            told.append(
                "Every scene is made from a product photo, so ask the shop owner to attach "
                "at least one."
            )
        else:
            job.status = Job.Status.PAGE_READ
            job.save(update_fields=["status"])
        return "\n".join(told)


PRODUCER = Agent(name="producer", purpose="produce", instructions=INSTRUCTIONS, tools=[ReadPage])
