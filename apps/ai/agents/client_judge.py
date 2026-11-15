"""Client judge: scores businesses as client leads against the organization's clients
brief, in batches. Separate from the sources that found them, so nothing grades its own
work. It sees only listed facts and the signals the code observed from them."""

import json
from html import escape

from apps.ai.schemas.clients import ClientVerdicts

ROLE = "client_judge"


def judge_businesses(runtime, businesses: list[dict]) -> ClientVerdicts:
    """`businesses`: the listing facts of each, numbered from 1 in the order given."""
    numbered = [{"number": index, **facts} for index, facts in enumerate(businesses, start=1)]
    return runtime.ask(
        ROLE,
        ClientVerdicts,
        organization=escape(runtime.organization_brief(), quote=False),
        businesses=escape(json.dumps(numbered, ensure_ascii=False, indent=1), quote=False),
    )
