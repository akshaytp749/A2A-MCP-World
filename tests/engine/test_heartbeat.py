"""Heartbeat pure-logic tests (TDD — committed before engine/heartbeat.py exists).

Adaptive cadence (FR-4.1), idle NPC rotation (FR-4.2), event-seed rolls (FR-4.2).
Pure functions only — the scheduler loop is exercised live, not unit-mocked.
"""

import random

from engine.heartbeat import npcs_due, roll_seeds, tick_interval_seconds

NPCS = ["darwin", "ramanujan", "socrates", "tesla", "twain"]


# ------------------------------------------------------------------- cadence


def test_interval_fast_when_human_connected():
    assert tick_interval_seconds(human_active=True) == 90  # lively while watched


def test_interval_slow_when_idle():
    assert tick_interval_seconds(human_active=False) == 2700  # 45 min, saves quota


# ------------------------------------------------------------------ rotation


def test_all_npcs_ticked_when_human_present():
    assert npcs_due(7, NPCS, human_active=True) == NPCS


def test_idle_tick_gives_subset_of_two():
    due = npcs_due(7, NPCS, human_active=False)
    assert len(due) == 2
    assert set(due) <= set(NPCS)


def test_idle_rotation_is_deterministic_and_covers_everyone():
    seen: set[str] = set()
    for tick in range(1, 6):  # 5 idle ticks x 2 = everyone at least once
        due = npcs_due(tick, NPCS, human_active=False)
        assert due == npcs_due(tick, NPCS, human_active=False)  # deterministic
        seen.update(due)
    assert seen == set(NPCS)


def test_rotation_handles_fewer_npcs_than_subset():
    assert npcs_due(3, ["twain"], human_active=False) == ["twain"]
    assert npcs_due(3, [], human_active=False) == []


# ---------------------------------------------------------------- event seeds


SEEDS = [
    {"p": 1.0, "event": "always fires"},
    {"p": 0.0, "event": "never fires"},
    {"p": 0.5, "event": "sometimes fires"},
]


def test_seed_roll_respects_probabilities():
    rng = random.Random(42)
    fired = roll_seeds(SEEDS, rng)
    assert "always fires" in fired
    assert "never fires" not in fired


def test_seed_roll_deterministic_with_seeded_rng():
    assert roll_seeds(SEEDS, random.Random(7)) == roll_seeds(SEEDS, random.Random(7))


# ------------------------------------------------------------- wall-clock phase


def test_clock_phase_tracks_real_hours():
    from engine.heartbeat import clock_phase_for

    assert clock_phase_for(2) == "small hours"
    assert clock_phase_for(6) == "morning"
    assert clock_phase_for(10) == "midday"
    assert clock_phase_for(14) == "afternoon"
    assert clock_phase_for(18) == "evening"
    assert clock_phase_for(22) == "night"
