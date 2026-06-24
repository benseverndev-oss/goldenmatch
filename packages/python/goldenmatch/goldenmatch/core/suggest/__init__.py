"""Config-suggestion adapter for goldenmatch.

Public surface::

    from goldenmatch.core.suggest import review_config, Suggestion, SuggestionsNativeRequired

``review_config(df, config)`` runs the dedupe pipeline internally, assembles
the three Arrow batches required by the native kernel, and returns a list of
:class:`Suggestion` dataclasses.  Raises :exc:`SuggestionsNativeRequired` when
the native wheel is absent.
"""
from goldenmatch.core.suggest.adapter import review_config
from goldenmatch.core.suggest.apply import apply_suggestion
from goldenmatch.core.suggest.types import Suggestion, SuggestionsNativeRequired

__all__ = ["review_config", "apply_suggestion", "Suggestion", "SuggestionsNativeRequired"]
