from django.core.management.base import BaseCommand, CommandError

from apps.ai.config.loader import load_models_file, resolve_role
from apps.ai.health import check_models


class Command(BaseCommand):
    help = (
        "Send one short prompt to every model: each tier, on each provider in use, with "
        "each of its keys. Makes real API calls. --roles lists who answers each role "
        "instead, without calls."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--tier",
            action="append",
            dest="tiers",
            help="Tier to check (repeatable). Defaults to every tier in models.yaml.",
        )
        parser.add_argument(
            "--roles",
            action="store_true",
            help="Show each role's providers and models, in the order they are tried.",
        )

    def handle(self, *args, tiers=None, roles=False, **options):
        if roles:
            for role in load_models_file().roles:
                resolved = resolve_role(role)
                order = "  ->  ".join(f"{p}:{m}" for p, m in resolved.options)
                self.stdout.write(f"{role:<22} {resolved.tier:<9} {order}")
            return
        checks = check_models(tiers)
        for check in checks:
            where = f"{check.tier:<9} {check.provider:<7} {check.key_name or '-':<17}"
            if check.ok:
                self.stdout.write(f"{where} {check.model:<24} {check.seconds:5.1f}s  {check.reply}")
            else:
                state = "BROKEN" if check.broken else "FAILED"
                self.stderr.write(f"{where} {check.model:<24} {state}  {check.error}")
        failed = [check.label for check in checks if not check.ok]
        if failed:
            raise CommandError(f"{len(failed)} check(s) failed: {', '.join(failed)}")
        self.stdout.write(self.style.SUCCESS(f"All {len(checks)} checks passed."))
