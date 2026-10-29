"""The old fix path goes (spec #247, ticket #262): fix plans, their rows, the Fix each recorded
and the fix check of each, with every key to them. A shop owner's photo a fix had dropped is
set aside first, so the scene still doesn't use it."""

from django.db import migrations, models


def set_aside_what_fixes_dropped(apps, schema_editor):  # type: ignore[no-untyped-def]
    reference_photo = apps.get_model("jobs", "ReferencePhoto")
    dropped = reference_photo.objects.filter(dropped_by__isnull=False, set_aside_at__isnull=True)
    for photo in dropped.select_related("dropped_by"):
        photo.set_aside_at = photo.dropped_by.created_at
        photo.save(update_fields=["set_aside_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0078_fix_step_input_and_pause_park"),
        ("chat", "0018_message_without_plan_or_routing"),
        ("agents", "0012_system_note_without_plan"),
    ]

    operations = [
        migrations.RunPython(set_aside_what_fixes_dropped, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="fix",
            name="one_fix_per_call_and_scene",
        ),
        migrations.RemoveField(
            model_name="referencephoto",
            name="added_by",
        ),
        migrations.RemoveField(
            model_name="referencephoto",
            name="dropped_by",
        ),
        migrations.RemoveField(
            model_name="scenestep",
            name="fix",
        ),
        migrations.RemoveField(
            model_name="fixcheck",
            name="fix",
        ),
        migrations.RemoveField(
            model_name="fixcheck",
            name="new",
        ),
        migrations.RemoveField(
            model_name="fixcheck",
            name="notice",
        ),
        migrations.RemoveField(
            model_name="fixcheck",
            name="old",
        ),
        migrations.RemoveField(
            model_name="fixcheck",
            name="step",
        ),
        migrations.RemoveConstraint(
            model_name="fixplan",
            name="one_proposed_plan_per_session",
        ),
        migrations.RemoveConstraint(
            model_name="fixplanscene",
            name="one_row_per_plan_and_scene",
        ),
        migrations.RemoveField(
            model_name="scenestep",
            name="plan_scene",
        ),
        migrations.RemoveField(
            model_name="scenestep",
            name="redraw_of",
        ),
        migrations.AlterField(
            model_name="scenestep",
            name="fix_goal",
            field=models.TextField(
                blank=True,
                help_text="For a door 2 step: what must be different, as the producer's instruction says it. Blank otherwise.",
            ),
        ),
        migrations.RemoveField(
            model_name="fix",
            name="asked",
        ),
        migrations.RemoveField(
            model_name="fix",
            name="brings_back",
        ),
        migrations.RemoveField(
            model_name="fix",
            name="confirmed_by",
        ),
        migrations.RemoveField(
            model_name="fix",
            name="job",
        ),
        migrations.RemoveField(
            model_name="fix",
            name="scene",
        ),
        migrations.DeleteModel(
            name="FixCheck",
        ),
        migrations.RemoveField(
            model_name="fixplan",
            name="confirmed_by",
        ),
        migrations.RemoveField(
            model_name="fixplan",
            name="job",
        ),
        migrations.RemoveField(
            model_name="fixplan",
            name="session",
        ),
        migrations.RemoveField(
            model_name="fixplanscene",
            name="brought_back",
        ),
        migrations.RemoveField(
            model_name="fixplanscene",
            name="fix",
        ),
        migrations.RemoveField(
            model_name="fixplanscene",
            name="plan",
        ),
        migrations.RemoveField(
            model_name="fixplanscene",
            name="scene",
        ),
        migrations.DeleteModel(
            name="Fix",
        ),
        migrations.DeleteModel(
            name="FixPlan",
        ),
        migrations.DeleteModel(
            name="FixPlanScene",
        ),
    ]
