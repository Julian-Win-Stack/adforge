from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.models import F


def keep_which_finished(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    # When they finished wasn't kept, so a checkpoint that had finished is taken to have
    # finished when it started.
    ToolCall = apps.get_model("agents", "ToolCall")
    ToolCall.objects.filter(finished=True).update(finished_at=F("created_at"))


class Migration(migrations.Migration):
    dependencies = [
        ("agents", "0001_checkpoints"),
    ]

    operations = [
        migrations.AddField(
            model_name="toolcall",
            name="finished_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(keep_which_finished, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="toolcall",
            name="finished",
        ),
        migrations.AddField(
            model_name="toolcall",
            name="asked_about",
            field=models.CharField(
                blank=True,
                help_text='What its result has the agent ask the shop owner, such as "length" '
                'or "scene 2", so a tool can tell whether they have answered since. Blank for '
                "nothing.",
                max_length=50,
            ),
        ),
    ]
