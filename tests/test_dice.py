import random

from src.core.dice import ENTRY_GUARANTEE_ATTEMPTS, roll_entry_die


def test_entry_die_is_guaranteed_six_from_the_guarantee_attempt_onward():
    rng = random.Random(0)
    for attempt in (ENTRY_GUARANTEE_ATTEMPTS, ENTRY_GUARANTEE_ATTEMPTS + 1, 50):
        assert roll_entry_die(attempt, rng) == 6


def test_entry_die_stays_within_valid_die_range_before_the_guarantee():
    rng = random.Random(1)
    for attempt in range(1, ENTRY_GUARANTEE_ATTEMPTS):
        for _ in range(200):
            assert 1 <= roll_entry_die(attempt, rng) <= 6


def test_entry_die_six_probability_climbs_with_each_attempt():
    rng = random.Random(2)
    n = 4000

    def six_rate(attempt: int) -> float:
        hits = sum(1 for _ in range(n) if roll_entry_die(attempt, rng) == 6)
        return hits / n

    rate_1 = six_rate(1)
    rate_2 = six_rate(2)
    rate_3 = six_rate(3)

    assert rate_1 < rate_2 < rate_3
