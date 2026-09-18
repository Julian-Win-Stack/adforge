from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

# Jobs from before questions were stored wait with no question to answer. These are the
# questions they would have been asked, as worded when this migration was written.
QUESTIONS = {
    "needs_working_link": (
        "working_link",
        "We couldn't read one product's page from that link. "
        "What's the link to the product's own page?",
    ),
    "needs_product_photos": (
        "product_photos",
        "The page had no product photo we could use. "
        "Can you upload at least one photo of the product?",
    ),
}


def ask_jobs_already_waiting(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    Job = apps.get_model("jobs", "Job")
    Question = apps.get_model("jobs", "Question")
    for job in Job.objects.filter(status__in=QUESTIONS, questions__isnull=True):
        kind, question = QUESTIONS[job.status]
        # The entry that put the job into waiting says why it had to ask.
        last_entry = job.activity.order_by("-seq").first()
        reason = last_entry.reason if last_entry else ""
        Question.objects.create(job=job, kind=kind, question=question, reason=reason)


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0004_plan_scenes_and_questions"),
    ]

    operations = [
        migrations.RunPython(ask_jobs_already_waiting, migrations.RunPython.noop),
    ]
