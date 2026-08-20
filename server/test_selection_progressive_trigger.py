"""Synthetic validation harness for the PROGRESSIVE (per-selection) selection trigger.

Standalone -- no real elicitation, no sockets, no live server path. Run it as:

    cd server && python3 test_selection_progressive_trigger.py   # needs numpy (the venv)

Covers the build-only pieces (none wired live):

  * dc_adapter.selection_percentile_by_var -- per-variable SelectionBias percentile
    over EVERY belief variable (selection has no axes to scope to), by reusing
    scoped_detailed_map([v]) + the unchanged dc_metric.selection_bias_percentile.
  * llm_trigger.evaluate_selection_progressive_trigger -- readiness gate
    (n_selected >= MIN_SELECTIONS) then Shiyao's reduction: fire on the single most
    extreme variable (max percentile >= threshold), reporting target_var.
  * llm_intervention.generate_selection_and_emit -- the non-blocking emission wrapper,
    tested by capturing the intervention inputs it threads into the shared core.

Mirrors test_scoped_dwell_trigger.py's pattern (a nonlocal `check`, pure asserts +
prints, exits non-zero on failure). It does NOT touch evaluate_summary_trigger, the
submit-time summary path, dc_metric, or any existing test.
"""
import asyncio

import numpy as np

import dc_adapter
import dc_metric
import llm_intervention
import llm_trigger

VARS = ("var_a", "var_b", "var_c")


def make_entry(consistency, weights):
    """One dc_map_detailed entry, pooled DC computed the same way
    _consistency_and_weights_for_teen does (Sum_v w_v*C_v / Sum_v w_v)."""
    num = sum(weights[v] * consistency[v] for v in consistency)
    den = sum(weights[v] for v in consistency)
    dc = 0.0 if den == 0.0 else num / den
    return {"dc": dc, "consistency": dict(consistency), "weights": dict(weights)}


def mouseout(teen_id, x=None, y=None):
    """A completed point-hover carrying the live axis attribute names
    get_current_axes reads (data.x.name / data.y.name)."""
    data = {"id": teen_id}
    if x is not None:
        data["x"] = {"name": x}
    if y is not None:
        data["y"] = {"name": y}
    return {"interactionType": "mouseout_item", "interactionDuration": 0, "data": data}


def filter_log(itype, attribute=None):
    """A response_list entry (a wrapped message) for a filter interaction, the store
    get_current_filters replays."""
    data = {} if attribute is None else {"attribute": attribute}
    return {"input_data": {"interactionType": itype, "data": data}}


def record_all_active(detailed, x="var_a", y="var_b", filtered="var_c"):
    """A client_record with ALL of VARS active: two on the x/y axes, one filtered.
    Lets the readiness tests keep asserting the full-variable dict now that the
    trigger scopes to get_currently_active_variables (empty active set -> no fire)."""
    return {"dc_map_detailed": detailed,
            "bias_logs": [mouseout("t0", x=x, y=y)],
            "response_list": [filter_log("filter_added", filtered)]}


def main():
    failures = 0

    def check(label, cond):
        nonlocal failures
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        if not cond:
            failures += 1

    MINSEL = llm_trigger.MIN_SELECTIONS  # 5: the "starts at the 5th selection" gate

    # 12 teens, 3 variables, equal weights. Selected group ("hot", t0..t5) is the
    # population MAX on a and c but the population MIN on b, so the three variables'
    # per-variable percentiles must come out clearly different -- high, low, high.
    w1 = {"var_a": 1.0, "var_b": 1.0, "var_c": 1.0}
    hot = {"var_a": 1.0, "var_b": -1.0, "var_c": 1.0}
    cold = {"var_a": -1.0, "var_b": 1.0, "var_c": -1.0}
    detailed = {}
    for i in range(6):
        detailed[f"t{i}"] = make_entry(hot, w1)
    for i in range(6, 12):
        detailed[f"t{i}"] = make_entry(cold, w1)
    selected5 = [f"t{i}" for i in range(5)]   # 5 hot teens -> readiness met

    # ===================================================================== #
    # dc_adapter.selection_percentile_by_var -- per-variable, all variables
    # ===================================================================== #
    print("selection_percentile_by_var -- one percentile per belief variable:")

    SEED = 20260818
    got = dc_adapter.selection_percentile_by_var(
        detailed, selected5, n_trials=2000, rng=np.random.default_rng(SEED))

    check("returns a percentile for EVERY belief variable (all 3)",
          set(got.keys()) == set(VARS))

    # --- exact composition: threading ONE seeded rng through scope-v-alone +
    # selection_bias_percentile, in variable order, must reproduce it bit-for-bit.
    # This proves the function is faithfully "scope each var alone, then the pooled
    # percentile call" and nothing else.
    rng = np.random.default_rng(SEED)
    expected = {}
    for v in VARS:
        scoped = dc_adapter.scoped_detailed_map(detailed, [v])
        scalar = {tid: e["dc"] for tid, e in scoped.items()}
        expected[v] = dc_metric.selection_bias_percentile(scalar, selected5, 2000, rng)
    check("each variable's percentile == scoping that variable alone (exact match)",
          got == expected)

    # --- directional sanity (independent of the composition check): selecting the
    # MAX on a/c lands high, the MIN on b lands low.
    print(f"    per-variable percentiles: "
          f"{ {k: round(v, 3) for k, v in got.items()} }")
    check("var_a (selected = pop MAX) -> percentile high (>= 0.9)",
          got["var_a"] >= 0.9)
    check("var_c (selected = pop MAX) -> percentile high (>= 0.9)",
          got["var_c"] >= 0.9)
    check("var_b (selected = pop MIN) -> percentile low (<= 0.1)",
          got["var_b"] <= 0.1)

    # --- guards inherited from selection_bias_percentile / the map shape ---------
    check("empty detailed map -> {} (no variables to score)",
          dc_adapter.selection_percentile_by_var({}, selected5) == {})
    none_ids = ["nope1", "nope2", "nope3", "nope4", "nope5"]  # absent from the map
    none_out = dc_adapter.selection_percentile_by_var(detailed, none_ids, n_trials=50)
    check("nothing selected present in map -> every variable's percentile is None",
          set(none_out.keys()) == set(VARS)
          and all(p is None for p in none_out.values()))

    # ===================================================================== #
    # selection_percentile_by_var(variables=...) -- score only the given subset
    # ===================================================================== #
    print("\nselection_percentile_by_var(variables=...) -- scoped subset only:")

    # --- variables=None (and omitted) preserves the full all-variables behavior ---
    full = dc_adapter.selection_percentile_by_var(
        detailed, selected5, n_trials=2000, rng=np.random.default_rng(SEED))
    full_explicit_none = dc_adapter.selection_percentile_by_var(
        detailed, selected5, n_trials=2000, rng=np.random.default_rng(SEED),
        variables=None)
    check("variables=None scores EVERY belief variable (backward-compatible)",
          set(full.keys()) == set(VARS))
    check("variables=None is identical to omitting it (same seed, exact match)",
          full_explicit_none == full)

    # --- a subset scores ONLY that subset, and matches the full run's values ------
    # Scoping is per-variable independent, so a subset's percentile for var v equals
    # scoping v alone with the same seed -- proving excluded vars are skipped, not
    # post-filtered (and that the kept ones are computed identically).
    subset = dc_adapter.selection_percentile_by_var(
        detailed, selected5, n_trials=2000, rng=np.random.default_rng(SEED),
        variables=["var_a", "var_c"])
    check("variables=[a,c] -> ONLY those two keys (var_b skipped entirely)",
          set(subset.keys()) == {"var_a", "var_c"})
    expected_ac = {}
    rng_ac = np.random.default_rng(SEED)
    for v in ("var_a", "var_c"):   # map order is a,b,c -> subset keeps a,c in that order
        scoped = dc_adapter.scoped_detailed_map(detailed, [v])
        scalar = {tid: e["dc"] for tid, e in scoped.items()}
        expected_ac[v] = dc_metric.selection_bias_percentile(scalar, selected5, 2000, rng_ac)
    check("subset percentiles == scoping just those vars alone (exact match)",
          subset == expected_ac)

    # --- a single-element subset ------------------------------------------------
    just_a = dc_adapter.selection_percentile_by_var(
        detailed, selected5, n_trials=500, variables=["var_a"])
    check("variables=[a] -> only var_a scored", set(just_a.keys()) == {"var_a"})

    # --- non-belief names in `variables` are dropped by the intersection ----------
    mixed = dc_adapter.selection_percentile_by_var(
        detailed, selected5, n_trials=500, variables=["var_a", "child_id", "nope"])
    check("non-belief names (child_id, nope) are intersected out; only var_a kept",
          set(mixed.keys()) == {"var_a"})

    # --- empty / all-miss intersections -> {} (no null-sampling attempted) --------
    check("variables=[] -> {} (empty intersection)",
          dc_adapter.selection_percentile_by_var(detailed, selected5, variables=[]) == {})
    check("variables all non-belief -> {} (total miss, no scoring)",
          dc_adapter.selection_percentile_by_var(
              detailed, selected5, variables=["child_id", "nope"]) == {})
    check("variables=... on an EMPTY map -> {} (map guard still first)",
          dc_adapter.selection_percentile_by_var({}, selected5, variables=["var_a"]) == {})

    # ===================================================================== #
    # evaluate_selection_progressive_trigger -- readiness gate, NO fire decision
    # ===================================================================== #
    print("\nevaluate_selection_progressive_trigger -- readiness + evidence, no fire:")

    # --- below readiness short-circuits BEFORE any percentile computation --------
    # The record has NO dc_map_detailed: if the function tried to compute, it would
    # KeyError. A clean not_ready return proves the readiness gate short-circuits.
    rec_nomap = {}   # deliberately missing "dc_map_detailed"
    below = llm_trigger.evaluate_selection_progressive_trigger(
        rec_nomap, [f"t{i}" for i in range(MINSEL - 1)])   # 4 < 5
    check("below MIN_SELECTIONS -> ready False, reason starts 'not_ready'",
          below["ready"] is False and below["reason"].startswith("not_ready"))
    check("below readiness -> percentile_by_var is None (no computation attempted)",
          below["percentile_by_var"] is None)
    check("below readiness -> n_selected reported (4)",
          below["n_selected"] == MINSEL - 1)

    # --- exactly at MIN_SELECTIONS is READY (the 5th selection turns it on) -------
    # The record now needs an ACTIVE variable set to get past the new no_active_vars
    # guard; record_all_active makes all of VARS active (2 on axes, 1 filtered) so
    # this still exercises the full-variable dict.
    rec = record_all_active(detailed)
    at = llm_trigger.evaluate_selection_progressive_trigger(rec, selected5)
    check("exactly MIN_SELECTIONS selections -> ready True, reason 'ok'",
          at["ready"] is True and at["reason"] == "ok" and at["n_selected"] == MINSEL)
    check("ready -> percentile_by_var is the full per-variable dict (all active)",
          isinstance(at["percentile_by_var"], dict)
          and set(at["percentile_by_var"].keys()) == set(VARS))

    # --- n_selected counts UNIQUE ids (matches the summary gate's dedup) ----------
    dup = ["t0", "t0", "t1", "t2", "t3", "t4"]   # 5 unique despite 6 entries
    at_dup = llm_trigger.evaluate_selection_progressive_trigger(rec, dup)
    check("n_selected counts unique ids (6 entries, 5 unique) -> ready",
          at_dup["n_selected"] == 5 and at_dup["ready"] is True)
    just_under = ["t0", "t0", "t1", "t2", "t3"]  # 4 unique -> not ready
    check("duplicates do not inflate readiness (4 unique < 5 -> not_ready)",
          llm_trigger.evaluate_selection_progressive_trigger(
              rec, just_under)["ready"] is False)

    # ===================================================================== #
    # Fire decision (Shiyao's reduction): fire on the single most extreme var.
    # The per-variable percentiles are stubbed here so the REDUCTION is tested
    # deterministically -- the real percentile computation is already verified
    # above against real maps (exact composition), so these two layers are
    # covered independently.
    # ===================================================================== #
    print("\nfire decision -- max-across-variables reduction:")
    THRESH = llm_trigger.SELECTION_PERCENTILE_THRESHOLD  # 0.80

    def reduce_with(fixed):
        """Run the trigger (readiness met) with selection_percentile_by_var stubbed
        to `fixed`, so only the reduction logic is under test. The stub returns the
        same dict regardless of scope (scoping-agnostic), so these cases cover the
        reduction alone -- the real scoping is verified separately above and below.
        The record carries axes (var_a/var_b active) purely to clear the new
        no_active_vars guard before scoring is reached."""
        orig = dc_adapter.selection_percentile_by_var
        dc_adapter.selection_percentile_by_var = (
            lambda det, sel, n_trials=1000, rng=None, variables=None: dict(fixed))
        try:
            rec_stub = {"dc_map_detailed": {"_": 1},          # truthy; content unused
                        "bias_logs": [mouseout("x", x="var_a", y="var_b")]}  # active set
            return llm_trigger.evaluate_selection_progressive_trigger(
                rec_stub, ["s0", "s1", "s2", "s3", "s4"])   # 5 -> ready
        finally:
            dc_adapter.selection_percentile_by_var = orig

    # --- multiple above threshold -> target is the HIGHEST, not first/arbitrary ---
    # var_b (0.97) is the highest but NOT first in iteration order (var_a is first),
    # and all three clear 0.80 -- so a first-found or arbitrary pick would fail.
    multi = reduce_with({"var_a": 0.85, "var_b": 0.97, "var_c": 0.82})
    print(f"    multi-above {{a:.85,b:.97,c:.82}} -> "
          f"fired={multi['fired']} target={multi['target_var']} pct={multi['target_percentile']}")
    check("multiple above threshold -> fired True",
          multi["fired"] is True)
    check("target is the HIGHEST percentile variable (var_b), not first-found",
          multi["target_var"] == "var_b" and multi["target_percentile"] == 0.97)
    check("full percentile_by_var still returned for logging",
          multi["percentile_by_var"] == {"var_a": 0.85, "var_b": 0.97, "var_c": 0.82})

    # --- exactly one above threshold -> that one is the target -------------------
    one = reduce_with({"var_a": 0.55, "var_b": 0.91, "var_c": 0.40})
    check("exactly one above threshold -> fired True, target is that var",
          one["fired"] is True and one["target_var"] == "var_b"
          and one["target_percentile"] == 0.91)

    # --- none above threshold -> fired False even though READY -------------------
    none_above = reduce_with({"var_a": 0.55, "var_b": 0.79, "var_c": 0.40})
    print(f"    none-above (max .79 < {THRESH}) -> "
          f"ready={none_above['ready']} fired={none_above['fired']} target={none_above['target_var']}")
    check("none above threshold -> ready True but fired False",
          none_above["ready"] is True and none_above["fired"] is False)
    check("no fire -> target_var and target_percentile are both None",
          none_above["target_var"] is None and none_above["target_percentile"] is None)
    check("no fire -> percentile_by_var still returned (shows how close it got)",
          none_above["percentile_by_var"] == {"var_a": 0.55, "var_b": 0.79, "var_c": 0.40})

    # --- boundary: exactly AT threshold fires (>=, not >) ------------------------
    at_thresh = reduce_with({"var_a": 0.10, "var_b": THRESH})
    check("percentile exactly == threshold fires (>=)",
          at_thresh["fired"] is True and at_thresh["target_var"] == "var_b")

    # --- None-valued variables are excluded from the max ------------------------
    with_nones = reduce_with({"var_a": None, "var_b": 0.90, "var_c": None})
    check("variables with None percentile are skipped; fire on the scored one",
          with_nones["fired"] is True and with_nones["target_var"] == "var_b")
    all_none = reduce_with({"var_a": None, "var_b": None})
    check("all percentiles None -> fired False, target None (no crash on empty max)",
          all_none["fired"] is False and all_none["target_var"] is None)

    # ===================================================================== #
    # Active-variable scoping: only the currently-active vars (axes + filters) are
    # scored. Uses the REAL selection_percentile_by_var end-to-end where the assertion
    # is about WHICH variables get scored, and a scope-respecting stub where the point
    # is the reduction excluding a would-be argmax.
    # ===================================================================== #
    print("\nactive-variable scoping -- score only axes + filtered vars:")

    # --- percentile_by_var keys match ONLY the active subset (real scoring) --------
    # var_a on the x axis + var_c filtered -> active {var_a, var_c}; var_b is neither.
    rec_scoped = {"dc_map_detailed": detailed,
                  "bias_logs": [mouseout("t0", x="var_a")],           # axis: var_a
                  "response_list": [filter_log("filter_added", "var_c")]}  # filter: var_c
    scoped = llm_trigger.evaluate_selection_progressive_trigger(rec_scoped, selected5)
    print(f"    active={{var_a(axis),var_c(filter)}} -> keys={set(scoped['percentile_by_var'])} "
          f"fired={scoped['fired']} target={scoped['target_var']}")
    check("ready with a non-empty active set",
          scoped["ready"] is True and scoped["reason"] == "ok")
    check("only the active vars {var_a, var_c} are scored (var_b excluded)",
          set(scoped["percentile_by_var"].keys()) == {"var_a", "var_c"})
    check("the inactive var_b never appears in the scored dict",
          "var_b" not in scoped["percentile_by_var"])

    # --- core correctness: a non-active var that WOULD be the all-vars argmax is NOT
    # the target once scoping is active. Scope-respecting stub so the percentiles are
    # deterministic: full-scoring makes var_b (0.99) the argmax, but var_b is inactive.
    full_scores = {"var_a": 0.90, "var_b": 0.99, "var_c": 0.30}

    def scoped_stub(det, sel, n_trials=1000, rng=None, variables=None):
        if variables is None:
            return dict(full_scores)     # all-vars view: var_b (0.99) would win
        wanted = set(variables)
        return {v: p for v, p in full_scores.items() if v in wanted}

    all_vars_view = scoped_stub(None, None)          # what all-variables scoring sees
    all_vars_argmax = max(all_vars_view, key=all_vars_view.get)
    check("setup: under all-variables scoring the argmax IS the (to-be-inactive) var_b",
          all_vars_argmax == "var_b")

    orig_spv = dc_adapter.selection_percentile_by_var
    dc_adapter.selection_percentile_by_var = scoped_stub
    try:
        # active = {var_a (axis), var_c (filter)} -- var_b, the global argmax, inactive.
        rec_core = {"dc_map_detailed": {"_": 1},
                    "bias_logs": [mouseout("t0", x="var_a")],
                    "response_list": [filter_log("filter_added", "var_c")]}
        core = llm_trigger.evaluate_selection_progressive_trigger(rec_core, selected5)
    finally:
        dc_adapter.selection_percentile_by_var = orig_spv
    print(f"    all-vars argmax=var_b(0.99); active={{a,c}} -> "
          f"fired={core['fired']} target={core['target_var']}")
    check("global argmax var_b is inactive -> excluded from the scored dict",
          "var_b" not in core["percentile_by_var"])
    check("fires on the top ACTIVE var (var_a=0.90), NOT the global argmax var_b",
          core["fired"] is True and core["target_var"] == "var_a"
          and core["target_percentile"] == 0.90)

    # --- empty active set -> no_active_vars guard, NO scoring attempted ------------
    # A sentinel replacement raises if scoring is reached; the guard must return first.
    def must_not_call(*a, **k):
        raise AssertionError("selection_percentile_by_var called despite empty active set")

    rec_empty = {"dc_map_detailed": detailed}   # no bias_logs, no response_list -> {}
    orig_spv2 = dc_adapter.selection_percentile_by_var
    dc_adapter.selection_percentile_by_var = must_not_call
    try:
        empty = llm_trigger.evaluate_selection_progressive_trigger(rec_empty, selected5)
    finally:
        dc_adapter.selection_percentile_by_var = orig_spv2
    check("empty active set -> ready False, reason 'no_active_vars'",
          empty["ready"] is False and empty["reason"] == "no_active_vars")
    check("empty active set -> percentile_by_var None, n_selected reported",
          empty["percentile_by_var"] is None and empty["n_selected"] == MINSEL)

    # --- total miss: active vars present but NONE are belief vars -> fired False ----
    # child_id is "active" (filtered) but not a belief variable, so the intersection
    # in selection_percentile_by_var is empty -> {} -> nothing clears -> no fire. This
    # is the natural empty-intersection path, NOT no_active_vars (the set is non-empty)
    # and NOT a raised error.
    rec_miss = {"dc_map_detailed": detailed,
                "response_list": [filter_log("filter_added", "child_id")]}
    miss = llm_trigger.evaluate_selection_progressive_trigger(rec_miss, selected5)
    print(f"    active={{child_id}} (non-belief) -> ready={miss['ready']} "
          f"fired={miss['fired']} pbv={miss['percentile_by_var']}")
    check("total miss (active but non-belief) -> ready True (set was non-empty)",
          miss["ready"] is True and miss["reason"] == "ok")
    check("total miss -> empty percentile_by_var via intersection, fired False",
          miss["percentile_by_var"] == {} and miss["fired"] is False
          and miss["target_var"] is None)

    # ===================================================================== #
    # generate_selection_and_emit -- the non-blocking emission wrapper.
    # The shared core is stubbed to CAPTURE the intervention inputs the wrapper
    # threads into it (the wrapper's whole job); this needs no API key or teens/
    # beliefs fixtures. Confirms weights/attention/bias_v/phase/event and that
    # target_var is threaded through as the steering axis.
    # ===================================================================== #
    print("\ngenerate_selection_and_emit -- well-formed emit inputs:")

    captured = {}

    async def fake_core(sio, sid_by_pid, pid, client_record, teens,
                        weights, attention, bias_v, phase, trigger_signal, event,
                        axes=None, force_variable=None):
        captured.update(dict(
            sio=sio, sid_by_pid=sid_by_pid, pid=pid, teens=teens,
            weights=weights, attention=attention, bias_v=bias_v, phase=phase,
            trigger_signal=trigger_signal, event=event, axes=axes,
            force_variable=force_variable))
        return True   # pretend a delivery happened

    selection = {"selection_bias": 0.2,
                 "selection_bias_v": {"var_a": 0.3, "var_b": -0.1, "var_c": 0.05},
                 "n_selected": 5}
    selected = ["t0", "t1", "t2", "t3", "t4"]
    target_var = "var_a"

    orig_core = llm_intervention._generate_and_emit
    llm_intervention._generate_and_emit = fake_core
    try:
        ret = asyncio.run(llm_intervention.generate_selection_and_emit(
            "SIO", "SIDMAP", "pid1", {"beliefs": {}}, selection,
            {"t0": {}}, selected, target_var))
    finally:
        llm_intervention._generate_and_emit = orig_core

    check("wrapper returns the core's delivery result",
          ret is True)
    check("weights = 1.0 per selected id (selection-weighted, like the summary path)",
          captured["weights"] == {tid: 1.0 for tid in selected})
    check("attention = {'selected_ids': selected} (the bin_group_candidates split)",
          captured["attention"] == {"selected_ids": selected})
    check("bias_v = selection_bias_v (the per-variable selection scores)",
          captured["bias_v"] == selection["selection_bias_v"])
    check("phase = 'realtime' (non-blocking nudge, NOT a submit-time check)",
          captured["phase"] == "realtime")
    check("event = 'llm_intervention' (the realtime event, NOT SUMMARY_EVENT)",
          captured["event"] == "llm_intervention"
          and captured["event"] != llm_intervention.SUMMARY_EVENT)
    check("target_var threaded as force_variable (hard override, not axes)",
          captured["force_variable"] == target_var)
    check("axes is None for the selection path (force_variable replaces the steer)",
          captured["axes"] is None)

    print("\n" + "=" * 72)
    print(f"{'ALL CHECKS PASSED' if failures == 0 else str(failures) + ' CHECK(S) FAILED'}")
    print("=" * 72)
    return failures


if __name__ == "__main__":
    import sys
    sys.exit(1 if main() else 0)
