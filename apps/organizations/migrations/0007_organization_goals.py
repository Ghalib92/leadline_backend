from django.db import migrations, models


def backfill_goals(apps, schema_editor):
    """Organizations from before goals searched for tenders and grants."""
    Organization = apps.get_model("organizations", "Organization")
    Organization.objects.update(goals=["tenders", "grants"])


class Migration(migrations.Migration):
    dependencies = [
        ("organizations", "0006_orgsource"),
    ]

    operations = [
        migrations.AddField(
            model_name="organization",
            name="goals",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RunPython(backfill_goals, migrations.RunPython.noop),
    ]
