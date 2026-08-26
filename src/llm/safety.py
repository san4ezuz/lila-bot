"""Lexical prescreen for player responses — first of the two safety layers from
docs/PLAN.md §8.4. The second layer (system-prompt instruction to the LLM) is added in Stage 5;
this module only needs to catch enough to set `safety_flag` and route to `response_message()`.

STUB word list — needs expert review before production (see docs/PLAN.md §16, open question on
safety contacts) and should stay a soft signal, not a hard gate: a false positive here degrades
to a slightly-off supportive message, never blocks the player from continuing.
"""

import re

_MARKERS = (
    r"покончить с собой",
    r"не хочу жить",
    r"хочу умереть",
    r"причинить себе вред",
    r"самоубийств",
    r"наложить на себя руки",
)

_PATTERN = re.compile("|".join(_MARKERS), re.IGNORECASE)


def screen(text: str) -> str | None:
    """Returns 'soft_referral' if the text matches a crisis marker, else None."""
    if _PATTERN.search(text):
        return "soft_referral"
    return None
