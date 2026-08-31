import random

DICE_SIDES = 6

# Entry (docs/RULES.md §2) is guaranteed no later than this attempt — see roll_entry_die().
ENTRY_GUARANTEE_ATTEMPTS = 4

# P(natural six) for pre-entry attempts before the guarantee kicks in, keyed by attempt number
# (1-based). Climbs each try so a player stuck at attempt 2-3 still feels like they're getting
# closer, instead of a flat 1/6 chance right up to a sudden guarantee. Any attempt not listed
# (i.e. beyond what's needed before the guarantee) falls back to a fair 1/6.
ENTRY_SIX_PROBABILITY: dict[int, float] = {1: 1 / 6, 2: 1 / 3, 3: 0.7}


def roll_dice(rng: random.Random) -> int:
    """Roll a single six-sided die using the supplied RNG (injectable for tests)."""
    return rng.randint(1, DICE_SIDES)


def roll_entry_die(attempt: int, rng: random.Random) -> int:
    """Roll the die for pre-entry attempt number `attempt` (1-based, counting this roll).

    The chance of a natural six climbs with each attempt and hits certainty at
    ENTRY_GUARANTEE_ATTEMPTS, so entry is guaranteed by then even without going through
    apply_roll()'s own `next_turn >= ENTRY_GUARANTEE_ATTEMPTS` fallback (kept there as a
    defense-in-depth safety net). Non-six outcomes are spread evenly across 1-5.
    """
    if attempt >= ENTRY_GUARANTEE_ATTEMPTS:
        return DICE_SIDES
    p_six = ENTRY_SIX_PROBABILITY.get(attempt, 1 / DICE_SIDES)
    if rng.random() < p_six:
        return DICE_SIDES
    return rng.randint(1, DICE_SIDES - 1)
