import pytest

from apps.ai.exceptions import PromptError
from apps.ai.prompts import available_versions, load_prompt


@pytest.fixture
def prompts_dir(tmp_path):
    judge = tmp_path / "judge"
    judge.mkdir()
    (judge / "v1.md").write_text("Old rubric for {{ org_name }}.")
    (judge / "v2.md").write_text('Score {{org_name}} for {{ opportunity }}. Reply {"score": 0}.')
    (judge / "v10.md.bak").write_text("ignored")
    (judge / "notes.md").write_text("ignored")
    return tmp_path


def test_latest_version_is_loaded_by_default(prompts_dir):
    prompt = load_prompt("judge", directory=prompts_dir)
    assert prompt.version == 2
    assert prompt.id == "judge/v2"
    assert available_versions("judge", prompts_dir) == [1, 2]


def test_specific_version(prompts_dir):
    assert load_prompt("judge", 1, directory=prompts_dir).id == "judge/v1"
    with pytest.raises(PromptError, match="judge/v3 does not exist"):
        load_prompt("judge", 3, directory=prompts_dir)


def test_render_fills_placeholders_and_leaves_single_braces(prompts_dir):
    prompt = load_prompt("judge", directory=prompts_dir)
    assert prompt.variables == {"org_name", "opportunity"}
    text = prompt.render(org_name="Acme", opportunity="Water grant")
    assert text == 'Score Acme for Water grant. Reply {"score": 0}.'


def test_render_rejects_missing_and_unexpected_variables(prompts_dir):
    prompt = load_prompt("judge", directory=prompts_dir)
    with pytest.raises(PromptError, match=r"missing variables \['opportunity'\]"):
        prompt.render(org_name="Acme")
    with pytest.raises(PromptError, match=r"unexpected variables \['typo'\]"):
        prompt.render(org_name="x", opportunity="y", typo="z")


def test_unknown_agent_and_unsafe_names(prompts_dir):
    with pytest.raises(PromptError, match="No prompt files"):
        load_prompt("supervisor", directory=prompts_dir)
    with pytest.raises(PromptError, match="Invalid agent name"):
        load_prompt("../secrets", directory=prompts_dir)


def test_shipped_ping_prompt_renders():
    assert "pong" in load_prompt("ping").render()


def test_shipped_researcher_prompts_render():
    assert load_prompt("researcher", 1).id == "researcher/v1"
    v2 = load_prompt("researcher")
    assert v2.id == "researcher/v2"
    assert v2.variables == {"opportunity_types", "max_picks", "organization", "results"}


def test_shipped_opportunity_analyst_prompt_renders():
    v1 = load_prompt("opportunity_analyst", 1)
    assert v1.id == "opportunity_analyst/v1"
    assert v1.variables == {"today", "organization", "opportunity"}
    latest = load_prompt("opportunity_analyst")
    assert latest.id == "opportunity_analyst/v2"
    assert latest.variables == {"today", "organization", "opportunity", "company_evidence"}
    rendered = v1.render(
        today="2026-10-12",
        organization="Test Org",
        opportunity="Test Opportunity",
    )
    assert "Test Org" in rendered
    assert "Test Opportunity" in rendered


def test_shipped_source_reader_prompt_renders():
    v1 = load_prompt("source_reader")
    assert v1.id == "source_reader/v1"
    assert v1.variables == {
        "today",
        "source_url",
        "source_label",
        "opportunity_types",
        "page_text",
        "links",
    }
    rendered = v1.render(
        today="2026-10-14",
        source_url="https://example.org/calls",
        source_label="Grants Page",
        opportunity_types="grant, tender",
        page_text="We have a 2026 climate innovation call open.",
        links="- Apply: https://example.org/calls/climate-2026",
    )
    assert "https://example.org/calls" in rendered
    assert "Grants Page" in rendered
    assert "climate innovation call" in rendered

