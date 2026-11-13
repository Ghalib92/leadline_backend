import json

from django.core.management.base import BaseCommand, CommandError

from apps.organizations.models import Organization

from ...choices import EvalSuite
from ...evals import (
    GOLDEN_DIR,
    golden_cases,
    load_golden,
    previous_run,
    proposal_cases,
    review_cases,
    run_suite,
)

SUMMARY = {
    EvalSuite.JUDGE: ("agreement", "precision", "recall", "false_positive_rate", "in_range"),
    EvalSuite.EXTRACTION: ("exact_cases",),
    EvalSuite.DRAFT: ("mean_score", "min_score"),
}


class Command(BaseCommand):
    help = (
        "Measure the agents on labeled cases and save the result (EvalRun) with the prompt "
        "and model versions used. Makes real model calls."
    )

    def add_arguments(self, parser):
        parser.add_argument("suite", choices=EvalSuite.values)
        parser.add_argument(
            "--dataset",
            default="golden",
            choices=["golden", "reviews", "proposals"],
            help="golden: hand-labeled file; reviews: an organization's decisions (judge); "
            "proposals: an organization's drafts (draft).",
        )
        parser.add_argument("--golden", default=str(GOLDEN_DIR / "demo.yaml"))
        parser.add_argument("--organization", help="Organization id (reviews, proposals).")
        parser.add_argument(
            "--prompt",
            action="append",
            default=[],
            metavar="ROLE=VERSION",
            help="Pin a prompt version, e.g. --prompt judge=1 (repeatable).",
        )
        parser.add_argument("--limit", type=int, help="Only the first N cases.")
        parser.add_argument("--label", default="", help="A note to find this run later.")

    def handle(self, suite, dataset, golden, organization, prompt, limit, label, **options):
        pinned = {}
        for item in prompt:
            role, _, version = item.partition("=")
            if not version.isdigit():
                raise CommandError(f"--prompt expects ROLE=NUMBER, got {item!r}.")
            pinned[role] = int(version)

        org = None
        if dataset == "golden":
            if suite == EvalSuite.DRAFT:
                raise CommandError("The draft suite runs on --dataset proposals.")
            data = load_golden(golden)
            cases = golden_cases(data, suite)
            name = f"golden:{data.get('name', golden)}"
        else:
            if not organization:
                raise CommandError(f"--dataset {dataset} needs --organization.")
            org = Organization.objects.filter(pk=organization).first()
            if org is None:
                raise CommandError(f"No organization {organization}.")
            if (dataset, suite) == ("reviews", EvalSuite.JUDGE):
                cases = review_cases(org)
            elif (dataset, suite) == ("proposals", EvalSuite.DRAFT):
                cases = proposal_cases(org)
            else:
                raise CommandError(f"The {suite} suite does not run on {dataset}.")
            name = f"{dataset}:{org.pk}"
        if limit:
            cases = cases[:limit]
        if not cases:
            raise CommandError("No cases to run.")

        def progress(number, total, result):
            mark = "error" if "error" in result else ("ok" if result["ok"] else "MISS")
            detail = f" score={result['score']}" if "score" in result else ""
            self.stdout.write(f"  [{number}/{total}] {mark:5} {result['id']}{detail}")

        self.stdout.write(f"Running {suite} on {name} ({len(cases)} cases)…")
        run = run_suite(
            suite,
            cases,
            dataset=name,
            pinned=pinned,
            label=label,
            organization=org,
            progress=progress,
        )
        before = previous_run(run)
        self.stdout.write("")
        self.stdout.write(f"prompts: {json.dumps(run.prompt_versions)}")
        self.stdout.write(f"models:  {json.dumps(run.models_used)}")
        self.stdout.write(f"tokens:  {run.usage.get('tokens', 0)}   errors: {run.errors}")
        for key in SUMMARY[suite]:
            value = run.metrics.get(key)
            line = f"{key:22} {value}"
            if before is not None and before.metrics.get(key) is not None and value is not None:
                line += f"   (previous {before.metrics[key]}, {before.created_at:%Y-%m-%d})"
            self.stdout.write(line)
        if suite == EvalSuite.EXTRACTION:
            for field, value in run.metrics["fields"].items():
                self.stdout.write(f"  {field:20} {value}")
        self.stdout.write(self.style.SUCCESS(f"Saved eval run {run.pk}."))
