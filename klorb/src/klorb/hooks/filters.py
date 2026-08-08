# © Copyright 2026 Aaron Kimball
"""Pure evaluation of a `HookConfigFilter` against a subject string — no I/O, no config
lookup, just string/regex matching and boolean composition.
"""

import re

from klorb.hooks.config import HookConfigFilter


def evaluate_filter(filter_: HookConfigFilter, subject: str) -> bool:
    """Evaluate `filter_` against `subject`: `matches` is exact equality, `pattern` is
    `re.search`, `contains` is substring, `any`/`all` recurse and combine with OR/AND,
    `not_` negates a nested filter. Every field the filter actually sets must hold for this to
    return `True`; a filter with none set is vacuously eligible.
    """
    if filter_.matches is not None and subject != filter_.matches:
        return False
    if filter_.pattern is not None and re.search(filter_.pattern, subject) is None:
        return False
    if filter_.contains is not None and filter_.contains not in subject:
        return False
    if filter_.any is not None and not any(evaluate_filter(f, subject) for f in filter_.any):
        return False
    if filter_.all is not None and not all(evaluate_filter(f, subject) for f in filter_.all):
        return False
    if filter_.not_ is not None and evaluate_filter(filter_.not_, subject):
        return False
    return True
