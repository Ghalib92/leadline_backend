import re
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.urls import reverse

from apps.ai.evals.metrics import compare_field, extraction_metrics, judge_metrics
from apps.ai.testing import use_scripted_models
from apps.discovery.tests.factories import make_opportunity
from apps.learning.evals import GOLDEN_DIR, golden_cases, load_golden
from apps.learning.models import EvalRun

from .helpers import review

pytestmark = pytest.mark.django_db(transaction=True)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def test_judge_metrics():
    cases = [(True, 80), (True, 40), (False, 70), (False, 10), (False, 20)]
    metrics = judge_metrics(cases, threshold=55)
    assert metrics["confusion"] == {"tp": 1, "fp": 1, "fn": 1, "tn": 2}
    assert metrics["agreement"] == 0.6
    assert (metrics["precision"], metrics["recall"]) == (0.5, 0.5)
    assert metrics["false_positive_rate"] == 0.333
    assert (metrics["mean_score_approved"], metrics["mean_score_rejected"]) == (60.0, 33.3)
    assert judge_metrics([], 55)["agreement"] is None


@pytest.mark.parametrize(
    ("field", "expected", "got", "ok"),
    [
        ("deadline", "2027-03-15", "2027-03-15T17:00", True),
        ("deadline", "2027-03-15", "2027-03-16", False),
        ("amount_max", 150000, 150_900.0, True),
        ("amount_max", 150000, 160000, False),
        ("funder", "Northbridge Foundation", "The Northbridge Foundation", True),
        ("funder", "Northbridge Foundation", "", False),
        ("opportunity_type", "grant", "Grant", True),
        ("is_open", False, False, True),
        ("currency", "", "", True),
    ],
)
def test_compare_field(field, expected, got, ok):
    assert compare_field(field, expected, got) is ok


def test_extraction_metrics():
    metrics = extraction_metrics([{"a": True, "b": True}, {"a": False, "b": True}, {"b": True}])
    assert metrics == {"cases": 3, "exact_cases": 0.667, "fields": {"a": 0.5, "b": 1.0}}


def test_the_golden_file_is_valid():
    data = load_golden(GOLDEN_DIR / "demo.yaml")
    judge = golden_cases(data, "judge")
    assert len(judge) >= 8 and {case.expected for case in judge} == {"approve", "reject"}
    assert all(case.organization["dossier"] for case in judge)
    assert len(golden_cases(data, "extraction")) >= 3


# ---------------------------------------------------------------------------
# The command (scripted models)
# ---------------------------------------------------------------------------


def judge_by_title(prompt):
    opportunity = prompt.rsplit("<opportunity>", 1)[1]
    good = any(word in opportunity for word in ("Digital Skills", "bootcamps", "Digital4"))
    return {"score": 80 if good else 20, "reasons": ["scripted"]}


def run(*args):
    out = StringIO()
    call_command("run_evals", *args, stdout=out)
    return out.getvalue()


def test_judge_on_the_golden_set(monkeypatch, settings):
    use_scripted_models(monkeypatch, settings, {"judge": judge_by_title})
    output = run("judge", "--label", "baseline")
    eval_run = EvalRun.objects.get()
    assert (eval_run.suite, eval_run.dataset, eval_run.label) == (
        "judge",
        "golden:demo",
        "baseline",
    )
    assert eval_run.metrics["agreement"] == 1.0 and eval_run.metrics["in_range"] == 1.0
    assert eval_run.prompt_versions == {"judge": "judge/v3"}
    assert eval_run.models_used == {"judge": "test-answer"}
    assert eval_run.cases == 8 and eval_run.errors == 0 and eval_run.usage["tokens"] > 0
    assert "agreement              1.0" in output

    # A second run with the old prompt shows the comparison.
    output = run("judge", "--prompt", "judge=1", "--limit", "3")
    latest = EvalRun.objects.order_by("-created_at").first()
    assert latest.prompt_versions == {"judge": "judge/v1"} and latest.cases == 3
    assert re.search(r"agreement +1.0 +\(previous 1.0, ", output)


def test_failed_answers_are_counted_not_fatal(monkeypatch, settings):
    use_scripted_models(monkeypatch, settings, {"judge": lambda prompt: "nope"})
    output = run("judge", "--limit", "2")
    eval_run = EvalRun.objects.get()
    assert eval_run.errors == 2 and eval_run.metrics["cases"] == 0
    assert "error" in output


def test_extraction_on_the_golden_set(monkeypatch, settings):
    def verifier(prompt):
        if "Youth Data Fund" in prompt:
            return {
                "is_opportunity": True,
                "is_open": True,
                "opportunity_type": "grant",
                "funder": "Northbridge Foundation",
                "amount_min": 25000,
                "amount_max": 150000,
                "currency": "USD",
                "deadline": "2027-03-16",
            }
        if "Innovation Prize" in prompt:
            return {
                "is_opportunity": True,
                "is_open": False,
                "opportunity_type": "award",
                "funder": "Coastal Communities Fund",
                "currency": "EUR",
                "deadline": "2023-06-30",
            }
        return {"is_opportunity": False, "is_open": False}

    use_scripted_models(monkeypatch, settings, {"verifier": verifier})
    run("extraction")
    metrics = EvalRun.objects.get().metrics
    assert metrics["exact_cases"] == 0.667  # the first one has the wrong deadline
    assert metrics["fields"]["deadline"] == 0.5 and metrics["fields"]["funder"] == 1.0


def test_judge_on_real_reviews_uses_only_earlier_decisions(
    monkeypatch, settings, auth_client, organization
):
    review(auth_client, organization, title="Road works A", reason="wrong_focus")
    review(auth_client, organization, title="Road works B", reason="wrong_focus")
    review(auth_client, organization, title="Digital Skills grant", decision="approve")
    prompts = []

    def judge(prompt):
        prompts.append(prompt)
        return judge_by_title(prompt)

    use_scripted_models(monkeypatch, settings, {"judge": judge})  # a model per case
    run("judge", "--dataset", "reviews", "--organization", organization.pk)
    eval_run = EvalRun.objects.get()
    assert eval_run.dataset == f"reviews:{organization.pk}" and eval_run.cases == 3
    assert eval_run.metrics["agreement"] == 1.0
    first, second = prompts[0], prompts[1]
    assert "(no similar past decisions yet)" in first  # nothing was decided before it
    memory = second.rsplit("<past_decisions>", 1)[1]
    assert 'REJECTED: "Road works A"' in memory and "Road works B" not in memory


def test_draft_quality_on_proposals(monkeypatch, settings, auth_client, organization):
    item = make_opportunity(organization, title="Youth Data Fund")
    path = reverse(
        "v1:opportunities:reviews",
        kwargs={"organization_id": organization.pk, "opportunity_id": item.pk},
    )
    auth_client.post(path, {"decision": "approve"}, format="json")  # fake writer drafts it
    grade = {
        "requirements_coverage": 8,
        "specificity": 6,
        "evidence_use": 5,
        "honesty": 9,
        "clarity": 7,
        "issues": ["Budget lacks figures"],
    }
    use_scripted_models(monkeypatch, settings, {"eval_grader": lambda prompt: grade})
    run("draft", "--dataset", "proposals", "--organization", organization.pk)
    eval_run = EvalRun.objects.get()
    assert eval_run.metrics == {"cases": 1, "mean_score": 70.0, "min_score": 70}
    assert eval_run.results[0]["grade"]["issues"] == ["Budget lacks figures"]


@pytest.mark.parametrize(
    "args",
    [
        ("draft",),
        ("judge", "--dataset", "reviews"),
        ("judge", "--dataset", "reviews", "--organization", "nope"),
        ("extraction", "--dataset", "proposals", "--organization", "{org}"),
        ("judge", "--prompt", "judge=latest"),
    ],
)
def test_command_validation(args, organization):
    args = [arg.replace("{org}", organization.pk) for arg in args]
    with pytest.raises(CommandError):
        run(*args)
