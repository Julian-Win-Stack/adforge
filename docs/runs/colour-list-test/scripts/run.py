"""Colour-list test (issue #110): how many photos does the planner's colour list leave out?

For each page in products.json, does what the app does when a shop owner sends the link:
read_page (Firecrawl, page check, text copy, photo pick, photos kept), then plan_ad, through
the producer's own tools, and stops there: no person, pictures, voices or clips. If the
planner asks the shop owner something, it is answered "Whatever you think is best." and the
ad planned again, at most twice. Pages run 2 at a time: 6 went over Firecrawl's rate limit.

    docker compose exec backend python scratch/colour-list-test/run.py [key ...]

Pays Firecrawl (3 calls a page) and OpenAI (page check, text copy, photo pick, plan). Each
page's result goes to outputs/<key>.json with its photos copied to outputs/<key>/; a page
with a result is skipped, so a run stopped part-way carries on."""

import json
import os
import shutil
import sys
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")

import django  # noqa: E402

django.setup()

from django.db import connection  # noqa: E402
from django.db.models import Sum  # noqa: E402
from django.utils import timezone  # noqa: E402

from adforge import file_store  # noqa: E402
from agents.models import ToolCall  # noqa: E402
from agents.producer import PlanAd, ReadPage  # noqa: E402
from chat import messages  # noqa: E402
from chat.models import Message, Session  # noqa: E402
from jobs.models import Job  # noqa: E402

OUT = HERE / "outputs"
# The shop owner's answer to any question the planner asks. The planner always needs a
# price, which the copied page text often leaves out (#110's write-up), so each page that
# asked for one is told the price its own page shows (answers.json).
PRICES: dict[str, str] = json.load(open(HERE / "answers.json"))


def owner_answer(key: str) -> str:
    price = f"The price is {PRICES[key]}, bought one-time. " if key in PRICES else ""
    return (
        f"{price}Where the page offers options, use the first one it shows. Leave out "
        "anything the page contradicts itself on."
    )


MOST_ASKS = 2


def _call(session: Session, job: Job | None, tool: str, arguments: dict[str, Any]) -> ToolCall:
    return ToolCall.objects.create(
        session=session,
        job=job,
        agent="producer",
        tool=tool,
        call_id=f"test-{uuid.uuid4().hex[:12]}",
        arguments=arguments,
    )


def _resumable(path: Path) -> dict[str, Any] | None:
    """A result whose page was read cleanly but which has no plan: its page is kept and only
    the planning is done again. A page Firecrawl turned away for its rate limit was read by
    the fallbacks, not as the app reads it, so it is read again from the start."""
    if not path.exists():
        return None
    before = json.load(open(path))
    if "job" not in before or any("429" in notice for notice in before.get("notices", [])):
        return None
    job = Job.objects.get(pk=before["job"])
    return before if job.status == Job.Status.PAGE_READ and job.photos.exists() else None


def run(key: str, url: str) -> dict[str, Any]:
    path = OUT / f"{key}.json"
    before = _resumable(path)
    if path.exists() and before is None:
        return json.load(open(path))
    result: dict[str, Any] = {"key": key, "url": url}
    try:
        if before is not None:
            result = {k: before[k] for k in ("key", "url", "session", "read_page", "job", "notices")}
            session = Session.objects.get(pk=before["session"])
            job = Job.objects.get(pk=before["job"])
            # The plans that asked before, each finished when it was made, so the answer
            # below comes after all of them.
            for call in job.tool_calls.filter(tool="plan_ad", finished_at__isnull=True):
                call.finished_at = call.created_at
                call.save(update_fields=["finished_at"])
            result["asked_before"] = before.get("asked_before", []) + before.get("asked", [])
            result["asked"] = []
            if result["asked_before"]:
                messages.add(session, role=Message.Role.USER, text=owner_answer(key))
        else:
            session = Session.objects.create()
            messages.add(session, role=Message.Role.USER, text=f"Make an ad for {url}")
            result["session"] = str(session.pk)
            read = _call(session, None, "read_page", {"link": url, "target_seconds": None})
            result["read_page"] = ReadPage(link=url, target_seconds=None).run(read)
            read.refresh_from_db()
            job = read.job
            assert job is not None
            result["job"] = job.pk
            result["notices"] = list(
                session.messages.filter(role=Message.Role.NOTICE).values_list("text", flat=True)
            )
            result["asked"] = []
        if job.status == Job.Status.PAGE_READ and job.photos.exists():
            while True:
                planned = _call(session, job, "plan_ad", {})
                said = PlanAd().run(planned)
                planned.result = said
                planned.finished_at = timezone.now()
                planned.save(update_fields=["result", "asked_about", "finished_at"])
                if not job.scenes.exists() and len(result["asked"]) < MOST_ASKS:
                    result["asked"].append(said)
                    messages.add(session, role=Message.Role.AGENT, text=said)
                    messages.add(session, role=Message.Role.USER, text=owner_answer(key))
                    continue
                result["plan_ad"] = said
                break
        job.refresh_from_db()
        result["status"] = job.status
        result["product_name"] = job.product_name
        result["product_colour"] = job.product_colour
        result["product_size"] = job.product_size
        result["scenes"] = [
            {"number": s.number, "line": s.line, "shows": s.shows} for s in job.scenes.all()
        ]
        folder = OUT / key
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        result["photos"] = []
        for photo in job.photos.order_by("position"):
            name = f"{photo.position}{Path(photo.file).suffix}"
            (folder / name).write_bytes(file_store.read(photo.file))
            result["photos"].append(
                {
                    "position": photo.position,
                    "file": name,
                    "source_url": photo.source_url,
                    "on_list": photo.shows_product_colour,
                }
            )
        cost = job.model_calls.aggregate(cost=Sum("cost_usd"))["cost"] or Decimal(0)
        result["model_cost_usd"] = float(cost)
        result["model_calls"] = list(
            job.model_calls.values_list("purpose", "outcome", "cost_usd").order_by("id")
        )
        result["model_calls"] = [[p, o, float(c or 0)] for p, o, c in result["model_calls"]]
        credits = 0
        for call, saved in job.firecrawl.get("files", {}).items():
            if call in ("page", "marked", "product"):
                answer = json.loads(file_store.read(saved))
                credits += int(answer.get("metadata", {}).get("creditsUsed") or 0)
        result["firecrawl_credits"] = credits
    except Exception as error:  # noqa: BLE001 - a failed page is a result too
        result["failed"] = f"{type(error).__name__}: {error}"[:1000]
        result["traceback"] = traceback.format_exc()[-3000:]
    finally:
        connection.close()
    OUT.mkdir(exist_ok=True)
    json.dump(result, open(path, "w"), indent=1, ensure_ascii=False, default=str)
    return result


def main() -> None:
    products: dict[str, str] = json.load(open(HERE / "products.json"))
    keys = sys.argv[1:] or list(products)
    with ThreadPoolExecutor(2) as pool:
        for r in pool.map(lambda k: run(k, products[k]), keys):
            if "failed" in r:
                print(f"{r['key']}: FAILED {r['failed'][:300]}")
                continue
            photos = r.get("photos", [])
            on_list = sum(p["on_list"] for p in photos)
            print(
                f"{r['key']}: {r['status']}, colour {r.get('product_colour')!r}, "
                f"on list {on_list}/{len(photos)}, asked {len(r.get('asked', []))}, "
                f"${r['model_cost_usd']:.3f} + {r['firecrawl_credits']} credits"
            )


if __name__ == "__main__":
    main()
