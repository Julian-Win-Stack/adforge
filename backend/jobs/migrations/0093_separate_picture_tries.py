from django.db import migrations, models
from django.db.models import F


def split_picture_tries(apps, schema_editor):
    Run = apps.get_model("jobs", "SceneAgentRun")
    Picture = apps.get_model("jobs", "ProducedItem")
    database = schema_editor.connection.alias
    for run in Run.objects.using(database).iterator():
        ends = Picture.objects.using(database).filter(kind="end_picture", sent__run=run.pk).count()
        if ends:
            Run.objects.using(database).filter(pk=run.pk).update(
                starting_pictures=run.starting_pictures - ends, end_pictures=ends
            )


def combine_picture_tries(apps, schema_editor):
    Run = apps.get_model("jobs", "SceneAgentRun")
    Run.objects.using(schema_editor.connection.alias).update(
        starting_pictures=F("starting_pictures") + F("end_pictures")
    )


class Migration(migrations.Migration):
    dependencies = [("jobs", "0092_produced_end_card")]

    operations = [
        migrations.RenameField(
            model_name="sceneagentrun", old_name="pictures", new_name="starting_pictures"
        ),
        migrations.AddField(
            model_name="sceneagentrun",
            name="end_pictures",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.RunPython(split_picture_tries, combine_picture_tries),
    ]
