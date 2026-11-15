"""Build business directory adapters from apps/ai/config/sources.yaml entries."""

from apps.ai.config.loader import SourceSpec
from apps.ai.exceptions import AIConfigError

BUSINESS_ADAPTERS = frozenset({"overpass", "apify_google_maps"})


def build_business_source(name: str, spec: SourceSpec):
    """Raises SourceOff when the source is switched off, SearchError when it can't run."""
    if spec.adapter == "overpass":
        from .osm import OverpassSource

        return OverpassSource(name, options=spec.options)
    if spec.adapter == "apify_google_maps":
        from .apify_maps import ApifyGoogleMapsSource

        return ApifyGoogleMapsSource(name, options=spec.options)
    raise AIConfigError(f"Business source {name!r} uses an unknown adapter {spec.adapter!r}.")
