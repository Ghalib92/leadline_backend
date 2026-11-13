"""Eval suites: run today's prompts and models on labeled cases and score them.

Datasets:
- golden: a hand-labeled YAML file (apps/learning/golden/), self-contained (it carries
  its own organization), so it works on any database.
- reviews: one organization's real decisions (each opportunity's latest review).
- proposals: one organization's current proposal drafts (draft suite).

Each run is saved as an EvalRun with the prompt and model versions it used, so a
prompt or model change can be compared with the previous run of the same suite.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from django.utils import timezone

from apps.ai.agents.judge import judge_fit
from apps.ai.agents.verifier import verify_listing
from apps.ai.config.loader import load_sources_file
from apps.ai.evals.metrics import compare_field, extraction_metrics, judge_metrics, mean_metrics
from apps.ai.exceptions import StructuredOutputError
from apps.ai.graphs.budget import Budget
from apps.ai.graphs.runtime import TaskRuntime
from apps.ai.schemas.proposal import DraftGrade
from apps.ai.tools.decisions import past_decisions
from apps.discovery.selectors import dossier_content, profile_context
from apps.opportunities.choices import ReviewDecision
from apps.opportunities.models import OpportunityReview
from apps.proposals.selectors import current_version

from .choices import EvalSuite
from .models import EvalRun

GOLDEN_DIR = Path(__file__).with_name("golden")
EVAL_BUDGET = {"max_searches": 0, "max_pages": 0, "max_tokens": 2_000_000, "max_wall_seconds": 3600}


@dataclass
class Case:
    id: str
    expected: object
    inputs: dict
    organization: dict  # name, profile, dossier
    extra: dict = field(default_factory=dict)


def load_golden(path: Path) -> dict:
    data = yaml.safe_load(Path(path).read_text())
    if not isinstance(data, dict) or "organization" not in data:
        raise ValueError(f"{path} is not a golden eval file.")
    return data


def golden_cases(data: dict, suite: str) -> list[Case]:
    organization = data["organization"]
    if suite == EvalSuite.JUDGE:
        return [
            Case(
                id=item["name"],
                expected=item["expected"],
                inputs=item["opportunity"],
                organization=organization,
                extra={
                    key: item[key]
                    for key in ("min_score", "max_score", "past_decisions")
                    if key in item
                },
            )
            for item in data.get("judge", [])
        ]
    if suite == EvalSuite.EXTRACTION:
        return [
            Case(
                id=item["name"],
                expected=item["expected"],
                organization=organization,
                inputs={"url": item["url"], "source": item.get("source", ""), "page": item["page"]},
            )
            for item in data.get("extraction", [])
        ]
    return []


def review_cases(organization) -> list[Case]:
    """Each opportunity's latest review becomes a judge case labeled by the person."""
    brief = {
        "name": organization.name,
        "profile": profile_context(organization),
        "dossier": dossier_content(organization),
    }
    latest_ids = (
        OpportunityReview.objects.filter(organization=organization)
        .order_by("opportunity_id", "-created_at", "-id")
        .distinct("opportunity_id")
        .values("id")
    )
    reviews = OpportunityReview.objects.filter(id__in=latest_ids).order_by("created_at")
    return [
        Case(
            id=review.pk,
            expected="approve" if review.decision == ReviewDecision.APPROVE else "reject",
            inputs=review.snapshot,
            organization=brief,
            extra={
                "memory": {
                    "organization_id": organization.pk,
                    "exclude_opportunity_id": review.opportunity_id,
                    "before": review.created_at,
                },
                "score_at_review": review.match_score,
            },
        )
        for review in reviews
    ]


def proposal_cases(organization) -> list[Case]:
    brief = {
        "name": organization.name,
        "profile": profile_context(organization),
        "dossier": dossier_content(organization),
    }
    cases = []
    for proposal in organization.proposals.select_related("opportunity"):
        version = current_version(proposal)
        if version is None:
            continue
        cases.append(
            Case(
                id=f"{proposal.pk}/v{version.number}",
                expected=None,
                inputs={"requirements": proposal.requirements, "sections": version.sections},
                organization=brief,
            )
        )
    return cases


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def _runtime(case: Case, pinned: dict) -> TaskRuntime:
    return TaskRuntime(
        budget=Budget(EVAL_BUDGET),
        organization_name=case.organization.get("name", ""),
        profile=case.organization.get("profile") or {},
        dossier=case.organization.get("dossier"),
        pinned_prompts=pinned,
        settings=load_sources_file().discovery,
    )


def run_suite(
    suite, cases, *, dataset, pinned=None, label="", organization=None, progress=None
) -> EvalRun:
    pinned = pinned or {}
    run = EvalRun.objects.create(
        suite=suite, dataset=dataset, organization=organization, label=label, cases=len(cases)
    )
    results, prompts, models, usage = [], {}, {}, {"tokens": 0}
    for number, case in enumerate(cases, start=1):
        runtime = _runtime(case, pinned)
        try:
            result = _SUITES[suite](runtime, case)
        except StructuredOutputError as exc:
            result = {"id": case.id, "error": str(exc)[:300], "ok": False}
        prompts.update(runtime.prompt_versions)
        models.update(runtime.models_used)
        usage["tokens"] += runtime.budget.tokens
        results.append(result)
        if progress:
            progress(number, len(cases), result)
    answered = [result for result in results if "error" not in result]
    run.results = results
    run.errors = len(results) - len(answered)
    run.metrics = _METRICS[suite](answered)
    run.prompt_versions = prompts
    run.models_used = models
    run.usage = usage
    run.finished_at = timezone.now()
    run.save()
    return run


def _judge_case(runtime, case: Case) -> dict:
    memory = []
    if "past_decisions" in runtime.prompt_for("judge").variables:
        if "memory" in case.extra:  # real reviews: only decisions made before this one
            memory = past_decisions(facts=case.inputs, **case.extra["memory"])
        else:
            memory = case.extra.get("past_decisions", [])
    verdict = judge_fit(runtime, case.inputs, memory)
    approved = case.expected == "approve"
    low, high = case.extra.get("min_score", 0), case.extra.get("max_score", 100)
    threshold = load_sources_file().discovery.match_threshold
    return {
        "id": case.id,
        "expected": case.expected,
        "score": verdict.score,
        "in_range": low <= verdict.score <= high,
        "ok": (verdict.score >= threshold) == approved,
        "reasons": verdict.reasons[:3],
        **(
            {"score_at_review": case.extra["score_at_review"]}
            if "score_at_review" in case.extra
            else {}
        ),
    }


def _judge_metrics(results):
    threshold = load_sources_file().discovery.match_threshold
    metrics = judge_metrics([(r["expected"] == "approve", r["score"]) for r in results], threshold)
    ranged = [r for r in results if "in_range" in r]
    metrics["in_range"] = (
        round(sum(r["in_range"] for r in ranged) / len(ranged), 3) if (ranged) else None
    )
    return metrics


def _extraction_case(runtime, case: Case) -> dict:
    got = verify_listing(runtime, **case.inputs).model_dump()
    fields = {
        name: compare_field(name, want, got.get(name)) for name, want in case.expected.items()
    }
    return {
        "id": case.id,
        "fields": fields,
        "ok": all(fields.values()),
        "got": {name: got.get(name) for name in case.expected},
    }


def _draft_case(runtime, case: Case) -> dict:
    import json
    from html import escape

    sections = case.inputs["sections"]
    draft = "\n\n".join(f"## {s['title']}\n{s.get('content', '')}" for s in sections)
    grade = runtime.ask(
        "eval_grader",
        DraftGrade,
        organization=escape(runtime.organization_brief(), quote=False),
        requirements=escape(
            json.dumps(case.inputs["requirements"], ensure_ascii=False), quote=False
        ),
        draft=escape(draft, quote=False),
    )
    return {
        "id": case.id,
        "score": grade.overall,
        "grade": grade.model_dump(),
        "ok": grade.overall >= 60,
    }


_SUITES = {
    EvalSuite.JUDGE: _judge_case,
    EvalSuite.EXTRACTION: _extraction_case,
    EvalSuite.DRAFT: _draft_case,
}
_METRICS = {
    EvalSuite.JUDGE: _judge_metrics,
    EvalSuite.EXTRACTION: lambda results: extraction_metrics([r["fields"] for r in results]),
    EvalSuite.DRAFT: lambda results: mean_metrics([r["score"] for r in results]),
}


def previous_run(run: EvalRun) -> EvalRun | None:
    return (
        EvalRun.objects.filter(suite=run.suite, dataset=run.dataset, finished_at__isnull=False)
        .exclude(pk=run.pk)
        .order_by("-created_at")
        .first()
    )
