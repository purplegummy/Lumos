"""Tests for the LLM-intervention pure helpers.

Runs with no extra dependencies:  python3 test_llm_intervention.py
Also discoverable by pytest if installed:  pytest test_llm_intervention.py

Covers the two grounded, deterministic pieces -- resolve_filter_range (numeric
and categorical) and derive_attended_direction. The Claude call itself is not
exercised here (it needs an API key and is off the critical path).

Expected filter ranges below are the values reviewed against the PoC's live
runs: screen time high -> [5, 8] and low -> [0, 3]; the categorical variable
high -> ["A little difficulty", "A lot of difficulty"] and low -> ["No difficulty"].
"""
import json
import os

from llm_intervention import (
    resolve_filter_range,
    _variable_for_filter_ranges,
    _majority_diagnosis,
    assemble_llm_input,
)
from llm_trigger import derive_attended_direction


# --- resolve_filter_range: numeric ------------------------------------------
def test_screen_time_high():
    """Screen time 'high' resolves to the top third of the observed [0, 8] range."""
    assert resolve_filter_range("screen_time_weekday", "high") == [5, 8]


def test_screen_time_low():
    """Screen time 'low' resolves to the bottom third of the observed [0, 8] range."""
    assert resolve_filter_range("screen_time_weekday", "low") == [0, 3]


def test_sleep_high_low():
    """Sleep hours over the observed [5, 11] range."""
    assert resolve_filter_range("hours_sleep_weeknight", "high") == [9, 11]
    assert resolve_filter_range("hours_sleep_weeknight", "low") == [5, 7]


def test_activity_high_low():
    """Physical activity over the observed [0, 7] range."""
    assert resolve_filter_range("days_physical_activity_week", "high") == [5, 7]
    assert resolve_filter_range("days_physical_activity_week", "low") == [0, 2]


def test_numeric_range_shape():
    """Numeric filter ranges are always a [lo, hi] pair of numbers."""
    lo, hi = resolve_filter_range("screen_time_weekday", "high")
    assert isinstance(lo, (int, float)) and isinstance(hi, (int, float)), (lo, hi)
    assert lo <= hi, (lo, hi)


# --- resolve_filter_range: categorical --------------------------------------
def test_categorical_high():
    """Categorical 'high' resolves to the labels above the lowest level."""
    assert resolve_filter_range("difficulty_making_friends", "high") == [
        "A little difficulty", "A lot of difficulty"
    ]


def test_categorical_low():
    """Categorical 'low' resolves to just the lowest level."""
    assert resolve_filter_range("difficulty_making_friends", "low") == ["No difficulty"]


def test_categorical_is_label_list():
    """Categorical filter ranges are a list of category-label strings, not [lo, hi]."""
    value = resolve_filter_range("difficulty_making_friends", "high")
    assert isinstance(value, list), value
    assert all(isinstance(v, str) for v in value), value


def test_unknown_variable_raises():
    """An unknown variable must raise -- the range has to be grounded in real data."""
    raised = False
    try:
        resolve_filter_range("not_a_real_variable", "high")
    except ValueError:
        raised = True
    assert raised, "expected ValueError for an unknown variable"


# --- derive_attended_direction ----------------------------------------------
# Synthetic teens: one high-screen-time / high-difficulty teen, one low, one mid.
TEENS = {
    "a": {"screen_time_weekday": 8, "difficulty_making_friends": "A lot of difficulty"},
    "b": {"screen_time_weekday": 1, "difficulty_making_friends": "No difficulty"},
    "c": {"screen_time_weekday": 4, "difficulty_making_friends": "A little difficulty"},
}
VARIABLES = ["screen_time_weekday", "difficulty_making_friends"]


def test_direction_higher_and_more():
    """Dwelling on the high-value teen skews numeric 'higher' and categorical 'more'."""
    direction = derive_attended_direction(TEENS, {"a": 1000.0}, VARIABLES)
    assert direction["screen_time_weekday"] == "higher", direction
    assert direction["difficulty_making_friends"] == "more_difficulty", direction


def test_direction_lower_and_less():
    """Dwelling on the low-value teen skews numeric 'lower' and categorical 'less'."""
    direction = derive_attended_direction(TEENS, {"b": 1000.0}, VARIABLES)
    assert direction["screen_time_weekday"] == "lower", direction
    assert direction["difficulty_making_friends"] == "less_difficulty", direction


def test_direction_weighted_by_dwell():
    """Direction follows where the dwell time is, not a plain count of teens."""
    # Heaviest dwell on the high teen, a little on the low teen -> still "higher".
    direction = derive_attended_direction(TEENS, {"a": 900.0, "b": 100.0}, VARIABLES)
    assert direction["screen_time_weekday"] == "higher", direction


def test_direction_omits_undwelled():
    """With no dwell anywhere, no direction is derived for any variable."""
    direction = derive_attended_direction(TEENS, {}, VARIABLES)
    assert direction == {}, direction


def test_direction_skips_missing_variable():
    """A variable absent from the data is simply omitted, not an error."""
    direction = derive_attended_direction(TEENS, {"a": 1000.0}, ["hours_sleep_weeknight"])
    assert direction == {}, direction


# --- _variable_for_filter_ranges (match themes back to candidates by value) --
# Two candidates: high vs low of the same variable, as build_candidate_themes
# produces. filter_ranges are the reliable key; position must not matter.
CANDIDATES = [
    {"predicate": {"screen_time_weekday": "high", "diagnosis": "no"}, "filter_ranges": [5, 8]},
    {"predicate": {"screen_time_weekday": "low", "diagnosis": "yes"}, "filter_ranges": [0, 3]},
]
CANDIDATES_CATEGORICAL = [
    {"predicate": {"difficulty_making_friends": "high", "diagnosis": "no"},
     "filter_ranges": ["A little difficulty", "A lot of difficulty"]},
    {"predicate": {"difficulty_making_friends": "low", "diagnosis": "yes"},
     "filter_ranges": ["No difficulty"]},
]


def test_match_returns_variable_by_value():
    """A theme's filter_ranges resolve to the variable of the matching candidate."""
    assert _variable_for_filter_ranges(CANDIDATES, [5, 8]) == "screen_time_weekday"
    assert _variable_for_filter_ranges(CANDIDATES, [0, 3]) == "screen_time_weekday"


def test_match_is_position_independent():
    """Matching is by value, not order: reversed candidates give the same result."""
    reversed_candidates = list(reversed(CANDIDATES))
    assert _variable_for_filter_ranges(reversed_candidates, [5, 8]) == "screen_time_weekday"
    assert _variable_for_filter_ranges(reversed_candidates, [0, 3]) == "screen_time_weekday"


def test_match_categorical():
    """Categorical filter_ranges (label lists) also key back to the variable."""
    assert _variable_for_filter_ranges(
        CANDIDATES_CATEGORICAL, ["No difficulty"]) == "difficulty_making_friends"


def test_no_match_returns_none():
    """filter_ranges matching no candidate exactly -> None (caller drops the filter)."""
    assert _variable_for_filter_ranges(CANDIDATES, [4, 7]) is None      # altered range
    assert _variable_for_filter_ranges(CANDIDATES, ["No difficulty"]) is None  # wrong type
    assert _variable_for_filter_ranges(CANDIDATES, None) is None


# --- _majority_diagnosis (must be dwell-WEIGHTED, not a head count) ----------
# The label column is ever_diagnosed_dep_or_anx; "Yes" == diagnosed.
DIAG_TEENS = {
    "d1": {"ever_diagnosed_dep_or_anx": "Yes"},
    "d2": {"ever_diagnosed_dep_or_anx": "Yes"},
    "d3": {"ever_diagnosed_dep_or_anx": "Yes"},
    "d4": {"ever_diagnosed_dep_or_anx": "Yes"},
    "n1": {"ever_diagnosed_dep_or_anx": "No"},
    "n2": {"ever_diagnosed_dep_or_anx": "No"},
    "n3": {"ever_diagnosed_dep_or_anx": "No"},
    "n4": {"ever_diagnosed_dep_or_anx": "No"},
    "n5": {"ever_diagnosed_dep_or_anx": "No"},
    "n6": {"ever_diagnosed_dep_or_anx": "No"},
}


def test_majority_weighted_yes():
    """Most dwell time on diagnosed teens -> 'Yes'."""
    dwell = {"d1": 8000.0, "d2": 6000.0, "n1": 1000.0}
    assert _majority_diagnosis(DIAG_TEENS, dwell) == "Yes"


def test_majority_weighted_no():
    """Most dwell time on non-diagnosed teens -> 'No'."""
    dwell = {"n1": 8000.0, "n2": 6000.0, "d1": 1000.0}
    assert _majority_diagnosis(DIAG_TEENS, dwell) == "No"


def test_majority_headcount_vs_time_disagreement():
    """The real-dataset case: fewer diagnosed teens but far more dwell time.

    4 diagnosed at 10s each (40s) vs 6 non-diagnosed at 1s each (6s) -> 87% of
    attention on diagnosed. A head count returns 'No' (4 < 6); the correct,
    dwell-weighted answer is 'Yes'. This is the bug the weighting fixes.
    """
    dwell = {"d1": 10000.0, "d2": 10000.0, "d3": 10000.0, "d4": 10000.0,
             "n1": 1000.0, "n2": 1000.0, "n3": 1000.0,
             "n4": 1000.0, "n5": 1000.0, "n6": 1000.0}
    assert _majority_diagnosis(DIAG_TEENS, dwell) == "Yes"


def test_majority_tie_returns_no():
    """Exactly half the dwell time on diagnosed -> 'No' (tie goes to 'No')."""
    dwell = {"d1": 5000.0, "n1": 5000.0}
    assert _majority_diagnosis(DIAG_TEENS, dwell) == "No"


def test_majority_empty_dwell_returns_no():
    """No dwell at all -> 'No'."""
    assert _majority_diagnosis(DIAG_TEENS, {}) == "No"


def test_majority_unlabeled_dwell_returns_no():
    """Dwelled teens with no label column contribute no weight -> 'No'."""
    teens = {"x": {"screen_time_weekday": 5}, "y": {"screen_time_weekday": 2}}
    assert _majority_diagnosis(teens, {"x": 9000.0, "y": 1000.0}) == "No"


# --- the five sample scenarios assemble correctly ---------------------------
# These are the same files run_llm_sample.py takes. No API call here -- assembly
# is pure; generation is network/key dependent and stays out of unit tests.
SAMPLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "llm_samples")
SAMPLE_FILES = [
    "sample_input_1_screen_time.json",
    "sample_input_2_sleep_focus.json",
    "sample_input_3_final_check.json",
    "sample_input_4_non_diagnosed_focus.json",
    "sample_input_5_edge_categorical.json",
]


def _load_sample(name):
    with open(os.path.join(SAMPLES_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def _assemble(name):
    return assemble_llm_input(_load_sample(name))


def test_all_samples_assemble():
    """Every sample assembles without error into the expected top-level shape."""
    for name in SAMPLE_FILES:
        llm_input = _assemble(name)
        for key in ("phase", "task_context", "current_focus", "evidence_for_focus",
                    "tone_constraints", "candidate_themes", "previous_interventions"):
            assert key in llm_input, (name, key)
        assert len(llm_input["candidate_themes"]) == 2, (name, llm_input["candidate_themes"])


def test_sample_numeric_filter_ranges():
    """Screen-time-driven sample -> numeric [lo, hi] ranges: high [5, 8], low [0, 3]."""
    ranges = [t["filter_ranges"] for t in _assemble(SAMPLE_FILES[0])["candidate_themes"]]
    assert ranges == [[5, 8], [0, 3]], ranges


def test_sample_sleep_filter_ranges():
    """Sleep-driven sample -> observed [5, 11] range: low [5, 7], high [9, 11]."""
    ranges = [t["filter_ranges"] for t in _assemble(SAMPLE_FILES[1])["candidate_themes"]]
    assert ranges == [[5, 7], [9, 11]], ranges


def test_sample_categorical_filter_ranges():
    """Categorical edge case -> label lists, never [lo, hi]."""
    ranges = [t["filter_ranges"] for t in _assemble(SAMPLE_FILES[4])["candidate_themes"]]
    assert ranges == [
        ["A little difficulty", "A lot of difficulty"],
        ["No difficulty"],
    ], ranges


def test_sample_final_check_phase_carried_through():
    """The final_check sample keeps its phase in the assembled input."""
    assert _assemble(SAMPLE_FILES[2])["phase"] == "final_check"


def test_sample_non_diagnosed_flips_predicates():
    """diagnosis_focus 'No' flips both theme predicates vs the diagnosed sample."""
    diagnosed = [t["predicate"]["diagnosis"] for t in _assemble(SAMPLE_FILES[0])["candidate_themes"]]
    reversed_ = [t["predicate"]["diagnosis"] for t in _assemble(SAMPLE_FILES[3])["candidate_themes"]]
    assert diagnosed == ["no", "yes"], diagnosed
    assert reversed_ == ["yes", "no"], reversed_


def test_sample_previous_interventions_passed_through():
    """The sleep sample's previous_interventions reach the assembled input."""
    assert len(_assemble(SAMPLE_FILES[1])["previous_interventions"]) == 1


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {t.__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return failures


if __name__ == "__main__":
    import sys
    sys.exit(1 if _run_all() else 0)
