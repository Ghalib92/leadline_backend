"""The "fake" runner's client search: sample businesses, no keys and no network.

Runs the same steps and events as apps.ai.graphs.clients, with "[Sample]" businesses
at example.org and a simple score, so the UI and the feed can be tried offline.
"""

import random
import time

from django.conf import settings

from apps.ai.events import RunEvent, RunEventEmitter
from apps.ai.graphs.discovery_runner import DiscoveryOutcome, DiscoveryRequest, DiscoverySink
from apps.ai.schemas.clients import Business
from apps.ai.schemas.discovery import Candidate

NAMES = ["Savannah", "Baobab", "Kilima", "Jacaranda", "Coral", "Acacia", "Mvule", "Tausi"]
KINDS = {
    "hospitality": "Hotel",
    "restaurants": "Restaurant",
    "health": "Clinic",
    "education": "Academy",
    "beauty": "Beauty Studio",
    "retail": "Boutique",
}


def run_fake_clients(
    request: DiscoveryRequest, emitter: RunEventEmitter, sink: DiscoverySink
) -> DiscoveryOutcome:
    rng = random.Random(request.run_id)
    plan = request.client_search or {}
    places = (plan.get("places") or ["Nairobi"])[:3]
    kinds = plan.get("customer_types") or ["hospitality"]

    def emit(step, message, **extra):
        emitter.emit(RunEvent(step=step, message=message, **extra))
        if settings.DISCOVERY_FAKE_STEP_SECONDS > 0:
            time.sleep(settings.DISCOVERY_FAKE_STEP_SECONDS)

    emit("profile", f"Reading the clients brief of {request.organization_name}")
    emit("profile", f"Planned {len(kinds) * len(places)} sample search(es)")
    businesses = []
    for place in places:
        for kind in kinds:
            emit(
                "search",
                f"Searching sample directory for {kind} in {place}",
                tool="business_search",
            )
            for _ in range(3):
                name = f"[Sample] {rng.choice(NAMES)} {KINDS.get(kind, 'Business')} {place}"
                has_site = rng.random() < 0.4
                businesses.append(
                    Business(
                        name=name,
                        source="sample",
                        source_label="Sample directory",
                        source_url=f"https://example.org/places/{rng.randint(1000, 9999)}",
                        category=KINDS.get(kind, "business").lower(),
                        city=place,
                        country=plan.get("country", ""),
                        phone=_phone(rng),
                        website=f"https://example.org/{rng.randint(1, 999)}" if has_site else "",
                        rating=round(rng.uniform(3.5, 4.9), 1),
                        reviews=rng.randint(5, 400),
                        complete_listing=True,
                    )
                )
    sink.progress(sources_scanned=1, pages_read=0, usage={"searches": len(kinds) * len(places)})
    emit("read", f"{len(businesses)} distinct sample businesses")
    emit("match", "Scoring sample businesses", tool="client_judge")
    added = 0
    for business in businesses:
        signals = business.observed_signals(busy_reviews=50)
        score = min(95, 45 + 20 * ("no_website" in signals) + 15 * ("many_reviews" in signals))
        reasons = [f"{business.category.capitalize()} in {business.city}, a kind you target"]
        if "no_website" in signals:
            reasons.append("No website on its listing")
        decision = sink.candidate(
            Candidate(
                title=business.name,
                summary=f"Sample {business.category} in {business.city}.",
                opportunity_type="client",
                source_name=business.source_label,
                source_url=business.website or business.source_url,
                location=business.city,
                match_score=score,
                match_reasons=reasons,
                business=business.as_details(
                    signals=signals, pitch="Sample pitch: offer a website with online booking."
                ),
            )
        )
        added += decision == "added"
    emit("save", f"{added} sample client lead(s) added to your feed")
    return DiscoveryOutcome(status="succeeded")


def _phone(rng) -> str:
    return f"+254 7{rng.randint(10, 99)} {rng.randint(100, 999)} {rng.randint(100, 999)}"
