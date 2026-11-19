"""Build source adapters from apps/ai/config/sources.yaml entries."""

from apps.ai.config.loader import SourceSpec
from apps.ai.exceptions import AIConfigError


def build_source(name: str, spec: SourceSpec):
    if spec.adapter == "tavily":
        from apps.ai.tools.search.tavily import TavilySearch

        from .web import WebSearchSource

        return WebSearchSource(name, TavilySearch(), domain=spec.domain, options=spec.options)
    if spec.adapter == "grants_gov":
        from .grants_gov import GrantsGovSource

        return GrantsGovSource(name, options=spec.options)
    if spec.adapter == "world_bank":
        from .world_bank import WorldBankSource

        return WorldBankSource(name, options=spec.options)
    if spec.adapter == "eu_funding":
        from .eu_funding import EuFundingSource

        return EuFundingSource(name, options=spec.options)
    if spec.adapter == "ted_europa":
        from .ted_europa import TedEuropaSource

        return TedEuropaSource(name, options=spec.options)
    if spec.adapter == "ppip":
        from .ppip import PPIPSource

        return PPIPSource(name, options=spec.options)
    if spec.adapter == "careers":
        from .careers import CareerPagesSource

        return CareerPagesSource(name, options=spec.options)
    raise AIConfigError(f"Source {name!r} uses an unknown adapter {spec.adapter!r}.")


KNOWN_ADAPTERS = frozenset(
    {"tavily", "grants_gov", "world_bank", "eu_funding", "ted_europa", "ppip", "careers"}
)
