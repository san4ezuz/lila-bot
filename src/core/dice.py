import random

DICE_SIDES = 6


def roll_dice(rng: random.Random) -> int:
    """Roll a single six-sided die using the supplied RNG (injectable for tests)."""
    return rng.randint(1, DICE_SIDES)
