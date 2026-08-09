"""
Locks core/analytics_dimensions.py's DIMENSION_REGISTRY and
core/analytics_metrics.py's METRIC_REGISTRY in sync with the actual query
wiring in api/routes_efficiency.py (_ASK_ENTITY_CONFIG_STATIC) so the
"machine-readable" registry can't silently drift from what's really
implemented -- a registry nobody keeps honest is worse than no registry.
"""
from api.routes_efficiency import _ASK_ENTITY_CONFIG_STATIC
from core.analytics_dimensions import DIMENSION_REGISTRY
from core.analytics_metrics import METRIC_REGISTRY, METRIC_KEYWORD_ALIASES

# entity id (routes_efficiency) -> dimension id (analytics_dimensions)
_ENTITY_TO_DIMENSION = {
    "person": "USER",
    "agent": "AGENT",
    "department": "DEPARTMENT",
    "account": "ACCOUNT",
    "platform": "PLATFORM",
    "model": "MODEL",
    "provider": "PROVIDER",
}


def test_every_static_entity_has_a_matching_dimension_registry_entry():
    for entity_id, (breakdown_key, filter_name, _label) in _ASK_ENTITY_CONFIG_STATIC.items():
        dimension_id = _ENTITY_TO_DIMENSION[entity_id]
        dimension = DIMENSION_REGISTRY[dimension_id]
        assert dimension.breakdown_key == breakdown_key, (
            f"{entity_id} -> {dimension_id}: breakdown_key mismatch "
            f"({dimension.breakdown_key!r} vs {breakdown_key!r})"
        )
        assert dimension.filter_name == filter_name, (
            f"{entity_id} -> {dimension_id}: filter_name mismatch "
            f"({dimension.filter_name!r} vs {filter_name!r})"
        )


def test_every_metric_keyword_alias_points_at_a_registered_metric():
    for metric_id, _phrases in METRIC_KEYWORD_ALIASES:
        assert metric_id in METRIC_REGISTRY, f"{metric_id!r} has keyword aliases but no MetricDefinition"


def test_every_dimension_supported_metric_is_registered():
    for dimension in DIMENSION_REGISTRY.values():
        for metric_id in dimension.supported_metrics:
            assert metric_id in METRIC_REGISTRY, (
                f"{dimension.id} lists unsupported metric {metric_id!r} "
                f"that has no MetricDefinition"
            )
