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
prints, exits non-zero on failure). It does NOT touch the dwell trigger, dc_metric,
or any existing test.
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
    rec = {"dc_map_detailed": detailed}
    at = llm_trigger.evaluate_selection_progressive_trigger(rec, selected5)
    check("exactly MIN_SELECTIONS selections -> ready True, reason 'ok'",
          at["ready"] is True and at["reason"] == "ok" and at["n_selected"] == MINSEL)
    check("ready -> percentile_by_var is the full per-variable dict",
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
        to `fixed`, so only the reduction logic is under test."""
        orig = dc_adapter.selection_percentile_by_var
        dc_adapter.selection_percentile_by_var = (
            lambda det, sel, n_trials=1000, rng=None: dict(fixed))
        try:
            rec_stub = {"dc_map_detailed": {"_": 1}}   # truthy; content unused (stub)
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
    check("event = 'llm_intervention' (the realtime event, not the old summary one)",
          captured["event"] == "llm_intervention"
          and captured["event"] != "llm_summary")
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
