"""S4 deterministic OFFLINE fake classifier: no provider, tokens or spending.

This is NOT evidence of semantic accuracy. It only exercises complete durable
classifier plumbing with exact quote grounding on RTB-approved leaf contexts.
"""
from __future__ import annotations

import re

from .v1_contracts import CONTRACT_VERSIONS, validate_model_output, reject

FAKE_MODEL = {
    "provider": "offline_fixture",
    "model_id": "sre-deterministic-contract-fixture",
    "model_version": "s4-v1",
    "prompt_version": "offline-no-prompt-v1",
}


def _find_quote(data, phrase):
    if not isinstance(phrase, str) or len(phrase.strip()) < 4:
        return None
    for field in ("title", "description"):
        value = data.get(field)
        if not isinstance(value, str):
            continue
        match = re.search(re.escape(phrase.strip()), value, flags=re.IGNORECASE)
        if match:
            return {"source": field, "quote": value[match.start():match.end()]}
    return None


def evaluate_fake(content, contexts):
    """One deterministic verdict per pinned hypothesis; never fabricate no_match.

    match: exact occurrence of the approved hypothesis name.
    no_match: exact occurrence of a nontrivial explicitly excluded phrase.
    otherwise: uncertain, never a fabricated negative.
    """
    if not isinstance(contexts, list) or not contexts or len(contexts) > 48:
        reject("contexts", "expected nonempty bounded hypothesis contexts")
    seen = set()
    labels = []
    for ctx in contexts:
        key = ctx["category_key"]
        if key in seen:
            reject("category_key", "duplicate fixture category")
        seen.add(key)
        hypothesis = ctx["hypothesis"]
        yes = _find_quote(content["data"], hypothesis["name"])
        no = _find_quote(content["data"], hypothesis["exclude"])
        if yes is not None:
            label = {"category_key": key, "decision": "match",
                     "reason": "Offline fixture: literal hypothesis name appears in source metadata; not a semantic judgment",
                     "evidence": [yes]}
        elif no is not None:
            label = {"category_key": key, "decision": "no_match",
                     "reason": "Offline fixture: literal exclusion phrase appears in source metadata; not a semantic judgment",
                     "evidence": [no]}
        else:
            label = {"category_key": key, "decision": "uncertain",
                     "reason": "Offline fixture has no literal grounded rule; a live model or human must evaluate",
                     "evidence": []}
        labels.append(label)
    result = {"schema_version": CONTRACT_VERSIONS["labels"], "labels": labels}
    return validate_model_output(result, [c["category_key"] for c in contexts], content)
