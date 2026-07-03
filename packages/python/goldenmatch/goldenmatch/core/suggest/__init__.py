"""Config-suggestion adapter for goldenmatch.

Public surface::

    from goldenmatch.core.suggest import review_config, Suggestion, SuggestionsNativeRequired

``review_config(df, config)`` runs the dedupe pipeline internally, assembles
the three Arrow batches required by the native kernel, and returns a list of
:class:`Suggestion` dataclasses.  Raises :exc:`SuggestionsNativeRequired` when
the native wheel is absent.

Self-verification (verify=True, the default) filters suggestions whose
application would worsen the score distribution's unsupervised health proxy.
See ``goldenmatch.core.suggest.health`` for the proxy formula.
"""
from goldenmatch.core.suggest.adapter import review_config, suggest_from_result
from goldenmatch.core.suggest.apply import apply_suggestion
from goldenmatch.core.suggest.health import suggestion_health_from_clusters
from goldenmatch.core.suggest.types import Suggestion, SuggestionsNativeRequired

# suggestion_health (scored-pairs proxy) is intentionally NOT in __all__: the
# pipeline returns only pairs >= threshold so mass_above is always 1.0 there,
# making the proxy meaningless on _run_pipeline output.  Access it explicitly
# via `from goldenmatch.core.suggest.health import suggestion_health`.
__all__ = [
    "review_config",
    "suggest_from_result",
    "apply_suggestion",
    "suggestion_health_from_clusters",
    "Suggestion",
    "SuggestionsNativeRequired",
]
