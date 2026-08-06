"""Tests for the LLM-intervention pure helpers.

Runs with no extra dependencies:  python3 test_llm_intervention.py
Also discoverable by pytest if installed:  pytest test_llm_intervention.py

Covers the deterministic pieces -- select_candidate_cell (which belief bin gets
recommended) and derive_attended_direction. The Claude call itself is not
exercised here (it needs an API key and is off the critical path), and neither is
dc_metric.bin_group_candidates, which has its own tests.
"""
from llm_intervention import (
    select_candidate_cell,
    _variable_for_filter_ranges,
    _majority_diagnosis,
    top_variable_contributors,
    assemble_llm_input,
)
from llm_trigger import derive_attended_direction


# --- select_candidate_cell --------------------------------------------------
def _cell(vc, underexploration, label="x"):
    return {"vc": vc, "underexploration": underexploration, "bin_label": label,
            "bin_range": [0, 1], "group": "diagnosed"}


def test_ranks_contradicting_cells_by_underexploration_times_vc():
    """Among underexplored, belief-contradicting cells, the largest product wins."""
    cells = [_cell(-0.2, 0.40, "weak_vc"), _cell(-0.9, 0.30, "winner"),
             _cell(-0.9, 0.10, "less_underexplored")]
    assert select_candidate_cell(cells)["bin_label"] == "winner"


def test_ignores_cells_the_participant_already_explored():
    """A strongly contradicting cell that is NOT underexplored is not a candidate."""
    cells = [_cell(-0.9, -0.30, "already_seen"), _cell(-0.1, 0.05, "underexplored")]
    assert select_candidate_cell(cells)["bin_label"] == "underexplored"


def test_falls_back_to_lowest_vc_when_nothing_contradicts():
    """No negative-vc cell -> the underexplored cell closest to a contrast."""
    cells = [_cell(0.8, 0.20, "consistent"), _cell(0.1, 0.05, "least_consistent")]
    assert select_candidate_cell(cells)["bin_label"] == "least_consistent"


def test_none_when_nothing_is_underexplored():
    """Everything already attended -> no recommendation, not a bogus one."""
    assert select_candidate_cell([_cell(-0.9, -0.1), _cell(0.5, 0.0)]) is None


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


# --- top_variable_contributors: raw, positive-only ranking ------------------
def test_ranking_is_raw_and_positive_only():
    """Raw descending: -0.9 no longer outranks +0.2, and 0.0 is excluded too."""
    ranked, _ = top_variable_contributors({
        "screen_time_weekday": -0.9,
        "hours_sleep_weeknight": 0.2,
        "days_physical_activity_week": 0.0,
        "difficulty_making_friends": 0.5,
    }, {})
    assert [v for v, _ in ranked] == ["difficulty_making_friends",
                                      "hours_sleep_weeknight"], ranked


def _session(bias_v, cells, values=()):
    """Minimal assemble_llm_input input: everything downstream of the metrics."""
    return {"dwell_bias_v": bias_v, "diagnosis_focus": "Yes", "bin_cells": cells,
            "attended_direction": {"screen_time_weekday": "higher"},
            "bin_values": list(values)}


def test_assembles_the_chosen_bin_as_the_filter_range():
    """The recommended cell's own bin becomes the filter, range and diagnosis both."""
    theme = assemble_llm_input(_session(
        {"screen_time_weekday": 0.4}, [_cell(-0.5, 0.2)], values=[0, 1]))["candidate_themes"][0]
    assert theme["filter_ranges"] == [0, 0], theme
    assert theme["predicate"]["diagnosis"] == "yes", theme


def test_half_open_bin_stops_short_of_the_next_bin():
    """[0, 1) must filter in the value 0 but never the value 1 the slider would
    otherwise include -- the filter has to match the cell that was scored."""
    cell = dict(_cell(-0.5, 0.2), bin_range=[0, 2], bin_label="[0, 2)")
    theme = assemble_llm_input(_session(
        {"screen_time_weekday": 0.4}, [cell], values=[0, 1, 2, 3]))["candidate_themes"][0]
    assert theme["filter_ranges"] == [0, 1], theme


def test_last_bin_keeps_its_closed_top_edge():
    """The final bin is closed in bin_label, so its top edge stays as given."""
    cell = dict(_cell(-0.5, 0.2), bin_range=[5, 8], bin_label="[5, 8]")
    theme = assemble_llm_input(_session(
        {"screen_time_weekday": 0.4}, [cell], values=[5, 6, 7, 8]))["candidate_themes"][0]
    assert theme["filter_ranges"] == [5, 8], theme


def test_assembles_categorical_bin_as_a_label_list():
    """A categorical bin is one label, wrapped in the list the multiselect wants."""
    cell = dict(_cell(-0.5, 0.2), bin_range="A lot of difficulty",
                bin_label="A lot of difficulty")
    theme = assemble_llm_input(_session(
        {"difficulty_making_friends": 0.4}, [cell]))["candidate_themes"][0]
    assert theme["filter_ranges"] == ["A lot of difficulty"], theme


def test_all_negative_assembles_to_nothing():
    """No positive variable -> no input at all, rather than a negative top variable."""
    assert assemble_llm_input(_session(
        {"screen_time_weekday": -0.4}, [_cell(-0.5, 0.2)])) is None


def test_no_qualifying_cell_assembles_to_nothing():
    """Every cell already explored -> no input at all, rather than an empty theme."""
    assert assemble_llm_input(_session(
        {"screen_time_weekday": 0.4}, [_cell(-0.5, -0.2)])) is None


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
