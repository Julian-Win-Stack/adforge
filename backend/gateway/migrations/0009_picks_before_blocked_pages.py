from django.db import migrations


def not_blocked(apps, schema_editor):
    """A photo pick answered before the picker said whether the page was blocked is replayed
    when its page is read again, so it gets the answer it would have had: not blocked."""
    ModelCall = apps.get_model("gateway", "ModelCall")
    for call in ModelCall.objects.filter(purpose="pick_photos", output__isnull=False):
        if isinstance(call.output, dict) and "page_blocked" not in call.output:
            call.output = {**call.output, "page_blocked": False}
            call.save(update_fields=["output"])


class Migration(migrations.Migration):
    dependencies = [
        ("gateway", "0008_music"),
    ]

    operations = [
        migrations.RunPython(not_blocked, migrations.RunPython.noop),
    ]
