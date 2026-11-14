from django.db import migrations, models

# Frozen copies of apps.discovery.choices.GOAL_SCOPES as they were when goals arrived.
TENDER_TYPES = {"tender", "rfp", "consultancy"}
GOAL_SOURCES = {
    "tenders": {"web", "procurement", "news"},
    "grants": {"web", "grants", "news"},
}


def assign_goals(apps, schema_editor):
    """An agent that only asked for tender types becomes a Tenders agent; every other
    one a Grants agent. Types and sources outside its goal are dropped."""
    SearchAgent = apps.get_model("discovery", "SearchAgent")
    for agent in SearchAgent.objects.all():
        types = agent.opportunity_types or []
        goal = "tenders" if types and set(types) <= TENDER_TYPES else "grants"
        agent.goal = goal
        agent.opportunity_types = [
            t for t in types if (t in TENDER_TYPES) == (goal == "tenders")
        ]
        agent.sources = [s for s in agent.sources or [] if s in GOAL_SOURCES[goal]]
        agent.save(update_fields=["goal", "opportunity_types", "sources"])


class Migration(migrations.Migration):
    dependencies = [
        ("discovery", "0009_run_dispatch"),
        ("organizations", "0007_organization_goals"),
    ]

    operations = [
        migrations.AddField(
            model_name="searchagent",
            name="goal",
            field=models.CharField(
                choices=[
                    ("clients", "Clients"),
                    ("tenders", "Tenders"),
                    ("grants", "Grants & funding"),
                ],
                default="grants",
                max_length=10,
            ),
        ),
        migrations.RunPython(assign_goals, migrations.RunPython.noop),
    ]
