import pytest

from apps.ai.config.loader import MODELS_FILE, load_models_file, resolve_role, resolve_tier
from apps.ai.exceptions import AIConfigError


def test_every_role_in_models_yaml_resolves():
    models = load_models_file()
    for role in models.roles:
        resolved = resolve_role(role)
        assert resolved.model == f"test-{resolved.tier}"


def test_role_overrides_defaults_and_keeps_the_rest():
    judge = resolve_role("judge")
    assert judge.tier == "answer"
    assert judge.reasoning_effort == "high"
    assert judge.max_tokens == 4096  # from defaults
    drafter = resolve_role("drafter")
    assert drafter.max_tokens == 8000  # role override
    assert drafter.timeout_seconds == 180


def test_model_id_comes_from_settings(settings):
    settings.AI_MODEL_TIERS = {**settings.AI_MODEL_TIERS, "answer": "provider/bigger-model"}
    assert resolve_role("supervisor").model == "provider/bigger-model"


def test_unknown_role_lists_known_roles():
    with pytest.raises(AIConfigError, match="Unknown AI role 'nope'.*judge"):
        resolve_role("nope")


def test_missing_tier_model_names_the_env_var(settings):
    settings.AI_MODEL_TIERS = {**settings.AI_MODEL_TIERS, "research": ""}
    with pytest.raises(AIConfigError, match="RESEARCH_MODEL"):
        resolve_role("researcher")


def test_resolve_tier_uses_defaults_only():
    resolved = resolve_tier("light")
    assert resolved.role == "tier:light"
    assert resolved.reasoning_effort is None
    assert resolved.max_tokens == 4096
    with pytest.raises(AIConfigError, match="Unknown AI tier"):
        resolve_tier("huge")


def test_role_on_undeclared_tier_is_rejected(tmp_path):
    path = tmp_path / "models.yaml"
    path.write_text("version: 1\ntiers: [light]\nroles:\n  judge: {tier: answer}\n")
    with pytest.raises(AIConfigError, match="undeclared tiers"):
        load_models_file(path)


@pytest.mark.parametrize(
    "body",
    [
        "version: 1\ntiers: [light]\nroles:\n  judge: {tier: light, temprature: 0}\n",  # typo
        "version: 1\ntiers: [light]\nroles:\n  judge: {tier: light, reasoning_effort: max}\n",
        "version: 1\ntiers: [light]\nroles: [not, a, mapping]\n",
        "version: 1\n  tiers: [broken yaml\n",
    ],
)
def test_invalid_models_file_raises_config_error(tmp_path, body):
    path = tmp_path / "models.yaml"
    path.write_text(body)
    with pytest.raises(AIConfigError, match="invalid"):
        load_models_file(path)


def test_missing_models_file_raises_config_error(tmp_path):
    with pytest.raises(AIConfigError, match="does not exist"):
        load_models_file(tmp_path / "absent.yaml")


def test_model_ids_cannot_be_hardcoded_in_models_yaml(tmp_path):
    # Model ids belong in .env; the YAML may only map roles to tiers.
    path = tmp_path / "models.yaml"
    path.write_text("version: 1\ntiers: [light]\nroles:\n  judge: {tier: light, model: x/y}\n")
    with pytest.raises(AIConfigError, match="invalid"):
        load_models_file(path)


def test_shipped_models_file_is_valid():
    assert load_models_file(MODELS_FILE).roles


def test_shipped_sources_file_is_valid():
    from apps.ai.config.loader import SOURCES_FILE, load_sources_file

    sources = load_sources_file(SOURCES_FILE)
    assert 0 <= sources.discovery.match_threshold <= 100
    assert {"web", "grants", "procurement", "news"} <= set(sources.source_categories)


def test_invalid_sources_file_raises_config_error(tmp_path):
    from apps.ai.config.loader import load_sources_file

    path = tmp_path / "sources.yaml"
    path.write_text(
        "version: 1\ndiscovery: {match_threshold: 150, budget: {}}\nsource_categories: {}\n"
    )
    with pytest.raises(AIConfigError, match="invalid"):
        load_sources_file(path)


def test_depth_presets_and_standard_matches_the_old_budget():
    from apps.ai.config.loader import load_sources_file

    discovery = load_sources_file().discovery
    assert set(discovery.depths) == {"quick", "standard", "deep"}
    assert discovery.budget_for("standard") == {
        "max_searches": 12,
        "max_pages": 40,
        "max_tokens": 200000,
        "max_wall_seconds": 600,
        "max_rounds": 2,
    }
    assert discovery.budget.max_searches == 12 and discovery.max_rounds == 2


def test_a_missing_depth_is_a_config_error(tmp_path):
    import yaml

    from apps.ai.config.loader import SOURCES_FILE, load_sources_file

    raw = yaml.safe_load(SOURCES_FILE.read_text())
    del raw["discovery"]["depths"]["deep"]
    path = tmp_path / "sources.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(AIConfigError, match="depths"):
        load_sources_file.__wrapped__(path)


def test_a_runs_depth_sets_its_rounds_and_old_runs_use_the_default():
    from types import SimpleNamespace

    from apps.ai.config.loader import load_sources_file
    from apps.ai.graphs.budget import Budget
    from apps.ai.graphs.discovery.nodes import _max_rounds

    discovery = load_sources_file().discovery
    deep = SimpleNamespace(settings=discovery, budget=Budget(discovery.budget_for("deep")))
    old = SimpleNamespace(settings=discovery, budget=Budget(discovery.budget.model_dump()))
    assert (_max_rounds(deep), _max_rounds(old)) == (3, 2)
