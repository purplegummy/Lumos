"""Tests for the LLM-intervention pure helpers.

Runs with no extra dependencies:  python3 test_llm_intervention.py
Also discoverable by pytest if installed:  pytest test_llm_intervention.py

Covers the deterministic pieces -- which belief bin gets recommended, how thin
ranges widen, and what the summary names. The Claude call itself is not exercised
here (it needs an API key and is off the critical path), and neither is
dc_metric.bin_group_candidates, which has its own tests.
"""
from llm_intervention import (
    select_candidate_cell,
    _variable_for_filter_ranges,
    _majority_diagnosis,
    top_variable,
    widened_span,
    assemble_llm_input,
)


def _cell(vc, underexploration, label="x", index=0, bin_range=None, group="diagnosed",
          dataset_share=1.0):
    return {"vc": vc, "underexploration": underexploration, "bin_label": label,
            "bin_index": index, "group": group, "dataset_share": dataset_share,
            "bin_range": [0, 1] if bin_range is None else bin_range}


def _row(counts):
    """One group's grid: bin i covers [i, i+1) and holds counts[i] teens sitting at
    value i. Only bin 0 is underexplored, so selection is deterministic; the rest
    are over-attended and belief-consistent, which is what the summary names."""
    total = sum(counts)
    cells = [_cell(-0.5 if i == 0 else 0.5, 0.1 if i == 0 else -0.1,
                   f"[{i}, {i + 1})", index=i,
                   bin_range=[i, i + 1], dataset_share=n / total)
             for i, n in enumerate(counts)]
    return cells, [i for i, n in enumerate(counts) for _ in range(n)]


# --- select_candidate_cell --------------------------------------------------
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


# --- _variable_for_filter_ranges (match themes back to candidates by value) --
# filter_ranges are the reliable key back to the candidate; position must not matter.
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


# --- top_variable: one variable drives everything ---------------------------
def test_top_variable_is_the_largest_positive():
    """Raw descending: -0.9 no longer outranks +0.2, and 0.0 is excluded too."""
    assert top_variable({
        "screen_time_weekday": -0.9,
        "hours_sleep_weeknight": 0.2,
        "days_physical_activity_week": 0.0,
        "difficulty_making_friends": 0.5,
    }) == "difficulty_making_friends"


def test_top_variable_ignores_unsupported_variables():
    """child_age_years has no filter row, so a positive score there is not a subject."""
    assert top_variable({"child_age_years": 9.0, "screen_time_weekday": -0.1}) is None


def _session(bias_v, cells, values=()):
    """Minimal assemble_llm_input input: everything downstream of the metrics."""
    return {"dwell_bias_v": bias_v, "diagnosis_focus": "Yes", "bin_cells": cells,
            "bin_values": list(values), "n_teens": len(values)}


def _theme(bias_v, cells, values=()):
    return assemble_llm_input(_session(bias_v, cells, values))["candidate_themes"][0]


# --- the recommended range --------------------------------------------------
def test_half_open_bin_stops_short_of_the_next_bin():
    """[0, 2) must filter in the value 1 but never the value 2 the slider would
    otherwise include -- the filter has to match the cells that were scored."""
    cell = dict(_cell(-0.5, 0.2), bin_range=[0, 2], bin_label="[0, 2)")
    assert _theme({"screen_time_weekday": 0.4}, [cell], [0, 1, 2, 3])["filter_ranges"] == [0, 1]


def test_last_bin_keeps_its_closed_top_edge():
    """The final bin is closed in bin_label, so its top edge stays as given."""
    cell = dict(_cell(-0.5, 0.2), bin_range=[5, 8], bin_label="[5, 8]")
    assert _theme({"screen_time_weekday": 0.4}, [cell], [5, 6, 7, 8])["filter_ranges"] == [5, 8]


def test_categorical_bin_is_a_label_list():
    """A categorical bin is its own label, in the list the multiselect wants."""
    cell = dict(_cell(-0.5, 0.2), bin_range="A lot of difficulty",
                bin_label="A lot of difficulty")
    theme = _theme({"difficulty_making_friends": 0.4}, [cell], ["A lot of difficulty"] * 5)
    assert theme["filter_ranges"] == ["A lot of difficulty"], theme


# --- widening to MIN_RECOMMENDED_TEENS --------------------------------------
def test_widens_a_thin_range_to_the_minimum():
    """A 1-teen bin grows into neighbours until the range holds 5 of the group."""
    cells, values = _row([1, 2, 4, 8])
    assert _theme({"screen_time_weekday": 0.4}, cells, values)["filter_ranges"] == [0, 2]


def test_widening_stops_at_full_width_when_the_minimum_is_out_of_reach():
    """Under 5 teens in the whole variable -> the recommendation still goes out."""
    cells, values = _row([1, 1])
    assert _theme({"screen_time_weekday": 0.4}, cells, values)["filter_ranges"] == [0, 1]


def test_widening_stays_inside_the_diagnosis_group():
    """Cells of the other group are never eligible, however many teens they hold."""
    cells, values = _row([1, 2, 4])
    other = [dict(c, group="not_diagnosed", underexploration=-0.5) for c in cells]
    span = widened_span(cells[0], cells + other, len(values))
    assert [c["group"] for c in span] == ["diagnosed"] * len(span), span
    assert [c["bin_index"] for c in span] == [0, 1, 2], span


# --- the summary reads the same grid ----------------------------------------
def test_summary_names_ranges_from_the_over_attended_side():
    """main_characteristics come from the NEGATIVE-underexploration cells."""
    cells, values = _row([1, 2, 4, 8])
    focus = assemble_llm_input(_session({"screen_time_weekday": 0.4}, cells, values))
    assert focus["current_focus"]["main_characteristics"] == [
        "screen time of 1", "screen time of 2", "screen time of 3"], focus["current_focus"]


def test_summary_skips_over_attended_cells_that_contradict_the_belief():
    """Lingering on a range that argues against your beliefs is not confirmation."""
    cells, values = _row([1, 2, 4, 8])
    for cell in cells[1:]:
        cell["vc"] = -0.5
    focus = assemble_llm_input(_session({"screen_time_weekday": 0.4}, cells, values))
    assert focus["current_focus"]["main_characteristics"] == [], focus["current_focus"]


def test_summary_and_recommendation_share_the_variable():
    """Both halves are about the one variable, so they cannot disagree."""
    cells, values = _row([1, 2, 4, 8])
    out = assemble_llm_input(_session({"screen_time_weekday": 0.4}, cells, values))
    named = {c["variable"] for c in out["evidence_for_focus"]["top_variable_contributors"]}
    assert named == {"screen time"}, named
    assert "screen time" in out["candidate_themes"][0]["raw_theme"]


# --- graceful no-ops --------------------------------------------------------
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
