import time

from django.core.management.base import BaseCommand, CommandError

from apps.ai.config.loader import load_models_file, resolve_tier
from apps.ai.llm import build_chat_model
from apps.ai.prompts import load_prompt


class Command(BaseCommand):
    help = (
        "Send one short prompt to each model tier to confirm the provider is reachable. "
        "Makes real API calls."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--tier",
            action="append",
            dest="tiers",
            help="Tier to check (repeatable). Defaults to every tier in models.yaml.",
        )

    def handle(self, *args, tiers=None, **options):
        tiers = tiers or load_models_file().tiers
        prompt = load_prompt("ping").render()
        failures = []
        for tier in tiers:
            resolved = resolve_tier(tier)
            started = time.monotonic()
            try:
                reply = build_chat_model(resolved).invoke(prompt)
            except Exception as exc:  # diagnostics: report every failure, keep going
                failures.append(tier)
                self.stderr.write(f"{tier:<9} {resolved.model:<24} FAILED  {exc}")
                continue
            elapsed = time.monotonic() - started
            text = str(reply.content).strip().replace("\n", " ")[:80]
            self.stdout.write(f"{tier:<9} {resolved.model:<24} {elapsed:5.1f}s  {text}")
        if failures:
            raise CommandError(f"{len(failures)} tier(s) failed: {', '.join(failures)}")
        self.stdout.write(self.style.SUCCESS("All tiers reachable."))
