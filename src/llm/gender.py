"""The player's grammatical gender, read from their own words — instead of asking it on the way
in (docs/ROADMAP.md, этап 3). Russian marks it in the first person past tense and short
adjectives: «я устала», «я сама», «я готова». Only an unambiguous signal counts: a text with
both forms, or none, says nothing.

The guide is told to stay neutral until this fires, but the model keeps slipping into the
masculine (scripts/eval_prompts.py) — an explicit instruction once the gender is known is what
actually holds.
"""

import re

# «я» + up to three words («я так и не») + the gendered word.
_PREFIX = r"\bя(?:\s+\w+){0,3}?\s+"
# Past tense: -ла/-лась (female), -л/-лся (male), with the -ёл/-ошёл family («пришёл»).
_FEMALE_VERB = r"\w+(?:ла|лась)"
_MALE_VERB = r"\w+(?:л|лся)"
_FEMALE_SHORT = r"(?:сама|готова|рада|уверена|должна|согласна|виновата|счастлива|нужна)"
_MALE_SHORT = r"(?:сам|готов|рад|уверен|должен|согласен|виноват|счастлив|нужен)"

_FEMALE = re.compile(_PREFIX + rf"(?:{_FEMALE_VERB}|{_FEMALE_SHORT})\b", re.IGNORECASE)
_MALE = re.compile(_PREFIX + rf"(?:{_MALE_VERB}|{_MALE_SHORT})\b", re.IGNORECASE)


def infer_gender(text: str) -> str | None:
    """'female', 'male' or None."""
    normalized = text.replace("ё", "е")
    female = _FEMALE.search(normalized) is not None
    male = _MALE.search(normalized) is not None
    if female == male:
        return None
    return "female" if female else "male"
