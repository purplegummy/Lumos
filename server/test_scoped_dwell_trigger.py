"""Synthetic validation harness for the AXIS-SCOPED dwell trigger.

Standalone -- no real elicitation, no sockets, no live server path. Run it as:

    cd server && python3 test_scoped_dwell_trigger.py    # needs numpy (the venv)

Covers the two pieces added for scoping the dwell trigger to the currently-visible
x/y axis variables:

  * dc_adapter.scoped_detailed_map -- re-pools each teen's DC over just the visible
    variables from their stored consistency/weights, leaving the input map intact.
  * llm_trigger.evaluate_trigger  -- now scores the DwellBias percentile on that
    scoped map instead of the pooled six-belief DC.

Mirrors test_dc_metric.py's pattern (a nonlocal `check`, pure asserts + prints,
exits non-zero on failure). It does NOT touch the existing get_current_axes tests
(test_llm_intervention.py) or the pooled percentile tests (test_dc_metric.py);
both keep passing unchanged.
"""
import numpy as np

import dc_adapter
import dc_metric
import llm_trigger

VARS = ("var_a", "var_b", "var_c")


def make_entry(consistency, weights):
    """One dc_map_detailed entry, with its pooled DC computed the same way
    _consistency_and_weights_for_teen does (Sum_v w_v*C_v / Sum_v w_v)."""
    num = sum(weights[v] * consistency[v] for v in consistency)
    den = sum(weights[v] for v in consistency)
    dc = 0.0 if den == 0.0 else num / den
    return {"dc": dc, "consistency": dict(consistency), "weights": dict(weights)}


def mouseout(teen_id, duration_ms, x=None, y=None):
    """A completed point-hover carrying dwell (ms) and, optionally, the live axis
    attribute names get_current_axes reads (data.x.name / data.y.name)."""
    data = {"id": teen_id}
    if x is not None:
        data["x"] = {"name": x}
    if y is not None:
        data["y"] = {"name": y}
    return {"interactionType": "mouseout_item",
            "interactionDuration": duration_ms,
            "data": data}


def main():
    failures = 0

    def check(label, cond):
        nonlocal failures
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        if not cond:
            failures += 1

    def raises(exc, fn):
        try:
            fn()
            return False
        except exc:
            return True

    # ===================================================================== #
    # scoped_detailed_map: re-pool DC over a variable subset
    # ===================================================================== #
    print("scoped_detailed_map -- re-pool over the visible subset:")

    # One teen, 3 variables, DELIBERATELY unequal weights so a wrong (unweighted)
    # mean would be caught. Hand-computed targets:
    #   pooled  = (2*0.5 + 1*-0.2 + 3*0.9) / (2+1+3) = 3.5/6      = 0.5833333...
    #   {a,b}   = (2*0.5 + 1*-0.2)         / (2+1)   = 0.8/3      = 0.2666666...
    #   {a}     = (2*0.5)                  / 2       = 0.5
    #   {c}     = (3*0.9)                  / 3       = 0.9
    consistency = {"var_a": 0.5, "var_b": -0.2, "var_c": 0.9}
    weights = {"var_a": 2.0, "var_b": 1.0, "var_c": 3.0}
    detailed = {"u": make_entry(consistency, weights)}
    pooled_dc = detailed["u"]["dc"]

    s_ab = dc_adapter.scoped_detailed_map(detailed, ["var_a", "var_b"])
    check("scoped {a,b} dc == hand-computed weighted mean over a,b",
          abs(s_ab["u"]["dc"] - 0.8 / 3.0) < 1e-9)

    s_a = dc_adapter.scoped_detailed_map(detailed, ["var_a", None])   # one axis None
    check("single visible var (other axis None) -> dc off that one var",
          abs(s_a["u"]["dc"] - 0.5) < 1e-9)

    s_c = dc_adapter.scoped_detailed_map(detailed, ["var_c"])
    check("scoped {c} dc == its own weighted value (0.9)",
          abs(s_c["u"]["dc"] - 0.9) < 1e-9)

    # Full-subset scope reproduces the pooled DC exactly (sanity that the re-pool
    # is the same formula, just over fewer terms).
    s_all = dc_adapter.scoped_detailed_map(detailed, list(VARS))
    check("scoped over ALL vars reproduces the pooled dc",
          abs(s_all["u"]["dc"] - pooled_dc) < 1e-9)

    # --- input map is never mutated; consistency/weights preserved -------------
    check("input map's pooled dc unchanged after scoping",
          detailed["u"]["dc"] == pooled_dc)
    check("scoped entry keeps original consistency dict (unchanged)",
          s_ab["u"]["consistency"] == consistency)
    check("scoped entry keeps original weights dict (unchanged)",
          s_ab["u"]["weights"] == weights)
    check("scoped 'dc' actually differs from pooled (proves it re-pooled)",
          s_ab["u"]["dc"] != pooled_dc)

    # --- partial miss: at least one visible var present -> scope to it ----------
    s_partial = dc_adapter.scoped_detailed_map(detailed, ["var_a", "not_a_belief"])
    check("partial miss (one present, one absent) scopes to the present one",
          abs(s_partial["u"]["dc"] - 0.5) < 1e-9)

    # --- fail-loud / guard edges ----------------------------------------------
    check("empty visible_vars raises ValueError",
          raises(ValueError, lambda: dc_adapter.scoped_detailed_map(detailed, [])))
    check("all-None visible_vars raises ValueError",
          raises(ValueError,
                 lambda: dc_adapter.scoped_detailed_map(detailed, [None, None])))
    check("no visible var in a teen's consistency raises KeyError",
          raises(KeyError,
                 lambda: dc_adapter.scoped_detailed_map(detailed, ["nope", "gone"])))

    # --- zero-weight subset -> 0.0 (the same 0/0 guard dc_for_teen uses) --------
    zero_w = {"u": make_entry({"var_a": 0.7, "var_b": 0.3},
                              {"var_a": 0.0, "var_b": 0.0})}
    s_zero = dc_adapter.scoped_detailed_map(zero_w, ["var_a", "var_b"])
    check("zero total weight over the subset -> dc 0.0 (no divide-by-zero)",
          s_zero["u"]["dc"] == 0.0)

    # ===================================================================== #
    # evaluate_trigger: fires off the SCOPED score, not the pooled one
    # ===================================================================== #
    print("\nevaluate_trigger -- scoped fire decision:")

    # 12 teens, weights 1 each. Built so that on the {var_a, var_b} axes the six
    # "target" teens are the population MAX, but pooled over all three they are the
    # population MIN (var_c cancels/inverts the a,b signal):
    #   target: a=+1 b=+1 c=-1  -> scoped{a,b}=+1.0 (max) , pooled=+1/3 (min)
    #   other : a=-1 b=-1 c=+5  -> scoped{a,b}=-1.0       , pooled=+1.0
    target_c = {"var_a": 1.0, "var_b": 1.0, "var_c": -1.0}
    other_c = {"var_a": -1.0, "var_b": -1.0, "var_c": 5.0}
    w1 = {"var_a": 1.0, "var_b": 1.0, "var_c": 1.0}

    detailed12 = {}
    targets = [f"t{i}" for i in range(6)]
    others = [f"t{i}" for i in range(6, 12)]
    for tid in targets:
        detailed12[tid] = make_entry(target_c, w1)
    for tid in others:
        detailed12[tid] = make_entry(other_c, w1)

    # Dwell ONLY on the targets: >= MIN_UNIQUE_HOVERS teens and >= MIN_TOTAL_DWELL
    # seconds so readiness clears, with var_a/var_b as the live axes (last hover).
    fire_logs = [mouseout(tid, 5000, x="var_a", y="var_b") for tid in targets]
    record = {"bias_logs": fire_logs, "dc_map_detailed": detailed12}
    metrics = dc_adapter.compute_dwell_metrics(detailed12, fire_logs)

    fired, reason, trace = llm_trigger.evaluate_trigger(record, metrics)
    scoped_pct = trace["dwell_bias_percentile"]
    print(f"    scoped percentile={scoped_pct}  reason={reason!r}")
    check("readiness cleared and reached the scoped percentile (pct not None)",
          scoped_pct is not None)
    check("scoped percentile at/above threshold -> FIRES",
          fired is True and reason == "ok")
    check("scoped percentile >= DWELL_PERCENTILE_THRESHOLD",
          scoped_pct is not None and scoped_pct >= llm_trigger.DWELL_PERCENTILE_THRESHOLD)

    # Contrast: the OLD pooled trigger would NOT have fired on the same dwell --
    # pooled puts the dwelled teens at the population floor. Seeded for determinism.
    dwell = dc_metric.dwell_by_teen(fire_logs)
    pooled_pct = dc_metric.dwell_bias_percentile(
        detailed12, dwell, n_trials=2000, rng=np.random.default_rng(0))
    print(f"    pooled percentile={pooled_pct} (same dwell, pooled DC)")
    check("pooled percentile on the same dwell is well BELOW threshold "
          "(proves the fire came off the scoped score)",
          pooled_pct is not None and pooled_pct < 0.10)

    # --- below_percentile: the logged observed is the SCOPED dwell_bias ---------
    # Same dwell, but axis var_c only: on that scope the dwelled teens sit at the
    # floor, so pct is far below threshold and it does not fire. The observed in the
    # reason must be the re-pooled dwell_bias over {var_c}, NOT the pooled value.
    c_logs = [mouseout(tid, 5000, x="var_c") for tid in targets]  # y axis unassigned
    rec_c = {"bias_logs": c_logs, "dc_map_detailed": detailed12}
    m_c = dc_adapter.compute_dwell_metrics(detailed12, c_logs)
    f_c, r_c, tr_c = llm_trigger.evaluate_trigger(rec_c, m_c)

    dwell_c = dc_metric.dwell_by_teen(c_logs)
    scoped_c = dc_adapter.scoped_detailed_map(detailed12, ["var_c", None])
    obs_scoped = dc_metric.dwell_bias(scoped_c, dwell_c)   # scoped to {var_c}
    obs_pooled = m_c["dwell_bias"]                          # the pooled DwellBias
    print(f"    reason={r_c!r}")
    print(f"    scoped observed={obs_scoped:+.4f}  pooled observed={obs_pooled:+.4f}")
    check("axis var_c: does not fire, reason is below_percentile",
          f_c is False and r_c.startswith("below_percentile"))
    check("scoped and pooled observed genuinely differ (test is meaningful)",
          abs(obs_scoped - obs_pooled) > 1e-6)
    check("reason logs the SCOPED observed (same scope as pct)",
          f"observed={obs_scoped:+.4f}" in r_c)
    check("reason does NOT log the pooled observed",
          f"observed={obs_pooled:+.4f}" not in r_c)

    # --- guard: no axes established yet -> not-ready, does not fire -------------
    # Same dwell (readiness clears) but the hovers carry no axis names.
    noaxis_logs = [mouseout(tid, 5000) for tid in targets]
    rec_noaxis = {"bias_logs": noaxis_logs, "dc_map_detailed": detailed12}
    m_noaxis = dc_adapter.compute_dwell_metrics(detailed12, noaxis_logs)
    f2, r2, tr2 = llm_trigger.evaluate_trigger(rec_noaxis, m_noaxis)
    check("no visible axes -> not fired, reason 'no_visible_axes', pct None",
          f2 is False and r2 == "no_visible_axes" and tr2["dwell_bias_percentile"] is None)

    # --- guard: axis carries a non-belief attribute -> scope_failed, no fire ----
    badaxis_logs = [mouseout(tid, 5000, x="child_id") for tid in targets]
    rec_bad = {"bias_logs": badaxis_logs, "dc_map_detailed": detailed12}
    m_bad = dc_adapter.compute_dwell_metrics(detailed12, badaxis_logs)
    f3, r3, tr3 = llm_trigger.evaluate_trigger(rec_bad, m_bad)
    check("non-belief axis -> not fired, reason starts 'scope_failed', pct None",
          f3 is False and r3.startswith("scope_failed")
          and tr3["dwell_bias_percentile"] is None)

    # ===================================================================== #
    # PER-VARIABLE recheck cooldown: one axis cooling never blocks the other
    # ===================================================================== #
    print("\nper-variable recheck cooldown:")
    RECHECK = llm_trigger.DWELL_RECHECK_SECONDS  # 10.0s of new dwell per var

    def hotcold_map(hot_c, cold_c, w):
        """12 teens: t0..t5 'hot' (dwelled), t6..t11 'cold' (the negation-ish)."""
        m = {}
        for i in range(6):
            m[f"t{i}"] = make_entry(hot_c, w)
        for i in range(6, 12):
            m[f"t{i}"] = make_entry(cold_c, w)
        return m

    def hot_dwell(x, y):
        """30s of dwell (6 unique teens x 5s) on the hot teens -> readiness clears."""
        return [mouseout(f"t{i}", 5000, x=x, y=y) for i in range(6)]

    # Map A: hot = pop MAX on both a and b (and pooled). Any scope over a/b fires.
    wAB = {"var_a": 1.0, "var_b": 1.0}
    mapA = hotcold_map({"var_a": 1.0, "var_b": 1.0},
                       {"var_a": -1.0, "var_b": -1.0}, wAB)

    # --- independent clocks: var_a cooling, var_b ready -> var_b still checked ---
    logsA = hot_dwell("var_a", "var_b")             # total 30.0s
    recA = {"bias_logs": logsA, "dc_map_detailed": mapA,
            "dwell_last_checked_by_var": {"var_a": 25.0}}   # a: 30-25=5 <10 (cooling)
    mA = dc_adapter.compute_dwell_metrics(mapA, logsA)
    fA, rA, trA = llm_trigger.evaluate_trigger(recA, mA)
    print(f"    a cooling / b ready -> fired={fA} reason={rA!r}")
    check("var_a cooling does NOT block var_b -> a check runs (pct not None)",
          trA["dwell_bias_percentile"] is not None)
    check("ready var (b) fires off its own scope",
          fA is True and rA == "ok")
    check("cooling var_a's clock is left untouched (25.0)",
          recA["dwell_last_checked_by_var"]["var_a"] == 25.0)
    check("checked var_b's clock advanced to current total dwell (30.0)",
          recA["dwell_last_checked_by_var"]["var_b"] == 30.0)
    check("fired check recorded ONLY var_b (the ready scope), for the dismiss rebase",
          recA["dwell_last_fired_vars"] == ["var_b"])

    # --- contrast: BOTH cooling -> too_soon, no check, no writes ----------------
    recA2 = {"bias_logs": logsA, "dc_map_detailed": mapA,
             "dwell_last_checked_by_var": {"var_a": 25.0, "var_b": 25.0}}
    fA2, rA2, trA2 = llm_trigger.evaluate_trigger(recA2, mA)
    check("both vars cooling -> too_soon, no check (pct None), no fire",
          fA2 is False and rA2.startswith("too_soon")
          and trA2["dwell_bias_percentile"] is None)
    check("too_soon writes nothing: both clocks still 25.0",
          recA2["dwell_last_checked_by_var"] == {"var_a": 25.0, "var_b": 25.0})

    # Map B: unequal weights so scope {a} and pooled {a,b} DISAGREE on the hot teens.
    #   hot  a=+1 b=-1  w=(1,4): scoped{a}=+1 (MAX), pooled=(1-4)/5=-0.6 (MIN)
    #   cold a=-1 b=+1        : scoped{a}=-1 (MIN), pooled=+0.6 (MAX)
    wB = {"var_a": 1.0, "var_b": 4.0}
    mapB = hotcold_map({"var_a": 1.0, "var_b": -1.0},
                       {"var_a": -1.0, "var_b": 1.0}, wB)
    logsB = hot_dwell("var_a", "var_b")             # total 30.0s
    dwellB = dc_metric.dwell_by_teen(logsB)
    mB = dc_adapter.compute_dwell_metrics(mapB, logsB)

    # --- both ready -> pools {a,b}: hot is the pooled MIN, so it does NOT fire ---
    recB_both = {"bias_logs": logsB, "dc_map_detailed": mapB}   # no clocks: both ready
    fB, rB, trB = llm_trigger.evaluate_trigger(recB_both, mB)
    obs_pool = dc_metric.dwell_bias(
        dc_adapter.scoped_detailed_map(mapB, ["var_a", "var_b"]), dwellB)
    print(f"    both ready (pool a,b) -> fired={fB} reason={rB!r}")
    check("both ready pools a+b (hot = pooled MIN) -> below_percentile, no fire",
          fB is False and rB.startswith("below_percentile"))
    check("both-ready reason logs the POOLED {a,b} observed (proves it pooled)",
          f"observed={obs_pool:+.4f}" in rB)
    check("both ready advanced BOTH clocks to 30.0",
          recB_both["dwell_last_checked_by_var"] == {"var_a": 30.0, "var_b": 30.0})

    # --- skip-and-check-single: b cooling -> scope {a} ONLY -> hot = MAX -> fires
    recB_a = {"bias_logs": logsB, "dc_map_detailed": mapB,
              "dwell_last_checked_by_var": {"var_b": 25.0}}   # b cooling, a ready
    fBa, rBa, trBa = llm_trigger.evaluate_trigger(recB_a, mB)
    print(f"    a only (skip b) -> fired={fBa} reason={rBa!r}")
    check("skip cooling b, check a only -> scope {a} (hot = MAX) FIRES "
          "where pooled would not (proves single-var scope)",
          fBa is True and rBa == "ok")
    check("single-var check advanced only var_a; var_b's cooldown untouched (25.0)",
          recB_a["dwell_last_checked_by_var"]["var_a"] == 30.0
          and recB_a["dwell_last_checked_by_var"]["var_b"] == 25.0)
    check("fired check over the single ready var records exactly [var_a]",
          recB_a["dwell_last_fired_vars"] == ["var_a"])

    # ===================================================================== #
    # reset_dwell_watermark rebases ONLY the fired check's variable(s)
    # ===================================================================== #
    print("\nreset_dwell_watermark -- rebase only the fired var(s):")

    # Fire covered var_b only (a was cooling). Then more dwell accrues while the
    # panel is up; dismiss must rebase var_b (to the new total) and leave var_a.
    logsR = hot_dwell("var_a", "var_b")
    recR = {"bias_logs": logsR, "dc_map_detailed": mapA,
            "dwell_last_checked_by_var": {"var_a": 25.0}}
    mR = dc_adapter.compute_dwell_metrics(mapA, logsR)
    fR, rR, _ = llm_trigger.evaluate_trigger(recR, mR)
    check("setup: fires on var_b only, records dwell_last_fired_vars=[var_b]",
          fR is True and recR["dwell_last_fired_vars"] == ["var_b"])
    # participant reads the intervention: +20s of dwell (t0) -> total 50s
    recR["bias_logs"].append(mouseout("t0", 20000, x="var_a", y="var_b"))
    llm_trigger.reset_dwell_watermark(recR)
    check("dismiss rebases the fired var_b to the new total dwell (50.0)",
          recR["dwell_last_checked_by_var"]["var_b"] == 50.0)
    check("dismiss leaves the non-fired var_a exactly where it was (25.0)",
          recR["dwell_last_checked_by_var"]["var_a"] == 25.0)
    check("dismiss consumes the fired-vars marker (no repeat rebase)",
          "dwell_last_fired_vars" not in recR)

    # Fire covered BOTH vars (both ready) -> dismiss rebases both.
    logsR2 = hot_dwell("var_a", "var_b")
    recR2 = {"bias_logs": logsR2, "dc_map_detailed": mapA}
    mR2 = dc_adapter.compute_dwell_metrics(mapA, logsR2)
    fR2, _, _ = llm_trigger.evaluate_trigger(recR2, mR2)
    recR2["bias_logs"].append(mouseout("t0", 20000, x="var_a", y="var_b"))  # -> 50s
    llm_trigger.reset_dwell_watermark(recR2)
    check("both-var fire then dismiss rebases BOTH clocks to 50.0",
          fR2 is True
          and recR2["dwell_last_checked_by_var"] == {"var_a": 50.0, "var_b": 50.0})

    # No fire on record -> reset is a no-op (nothing to rebase).
    recR3 = {"bias_logs": hot_dwell("var_a", "var_b"), "dc_map_detailed": mapA,
             "dwell_last_checked_by_var": {"var_a": 7.0}}
    llm_trigger.reset_dwell_watermark(recR3)
    check("reset with no dwell_last_fired_vars is a no-op",
          recR3["dwell_last_checked_by_var"] == {"var_a": 7.0})

    # ===================================================================== #
    # Active-filter scope: a filtered variable joins x/y as an "active" variable
    # (get_currently_active_variables unions axes + get_current_filters).
    # ===================================================================== #
    print("\nactive-filter scope (filtered vars join the axis vars):")

    def filter_log(itype, attribute=None):
        """A response_list entry (a wrapped message) for a filter interaction."""
        data = {} if attribute is None else {"attribute": attribute}
        return {"input_data": {"interactionType": itype, "data": data}}

    # Map C: 3 vars, hot teens are the population MAX on each -> any single-var scope
    # fires on the hot dwell.
    wC = {"var_a": 1.0, "var_b": 1.0, "var_c": 1.0}
    mapC = hotcold_map({"var_a": 1.0, "var_b": 1.0, "var_c": 1.0},
                       {"var_a": -1.0, "var_b": -1.0, "var_c": -1.0}, wC)
    noaxis_dwell = [mouseout(f"t{i}", 5000) for i in range(6)]   # 30s, NO axis names
    mC = dc_adapter.compute_dwell_metrics(mapC, noaxis_dwell)

    # --- a filtered-but-not-on-axis variable is scored and fires on its own ------
    recF = {"bias_logs": noaxis_dwell, "dc_map_detailed": mapC,
            "response_list": [filter_log("filter_added", "var_c")]}
    fF, rF, trF = llm_trigger.evaluate_trigger(recF, mC)
    print(f"    filter-only var_c (no axes) -> fired={fF} reason={rF!r}")
    check("no axes but var_c filtered -> it is the active scope and FIRES",
          fF is True and rF == "ok")
    check("the filtered var got scored (pct not None)",
          trF["dwell_bias_percentile"] is not None)
    check("the filtered var got its own cooldown entry (30.0)",
          recF["dwell_last_checked_by_var"]["var_c"] == 30.0)
    check("fired check recorded the filtered var",
          recF["dwell_last_fired_vars"] == ["var_c"])

    # --- filter var has an INDEPENDENT clock from an axis var --------------------
    # var_a on the x axis is cooling; var_c (filter-only) is fresh -> var_c is checked
    # and var_a is left alone. Same independent-clock guarantee as two axis vars.
    axis_a_dwell = [mouseout(f"t{i}", 5000, x="var_a") for i in range(6)]   # 30s
    recFI = {"bias_logs": axis_a_dwell, "dc_map_detailed": mapC,
             "response_list": [filter_log("filter_added", "var_c")],
             "dwell_last_checked_by_var": {"var_a": 25.0}}   # var_a cooling (5 < 10)
    mFI = dc_adapter.compute_dwell_metrics(mapC, axis_a_dwell)
    fFI, rFI, _ = llm_trigger.evaluate_trigger(recFI, mFI)
    check("cooling axis var_a does NOT block the filtered var_c -> fires on var_c",
          fFI is True and rFI == "ok")
    check("cooling axis var_a's clock is untouched (25.0)",
          recFI["dwell_last_checked_by_var"]["var_a"] == 25.0)
    check("filter var_c got its own independent clock (30.0)",
          recFI["dwell_last_checked_by_var"]["var_c"] == 30.0)
    check("fired check recorded ONLY the ready filter var (var_c)",
          recFI["dwell_last_fired_vars"] == ["var_c"])

    # --- non-belief filter var alongside a valid belief var -> graceful drop -----
    # var_a is on the axis (a real belief var); "child_id" is filtered but is NOT a
    # belief variable, so scoped_detailed_map drops it (partial miss) and scores var_a
    # alone -> fires, NOT scope_failed.
    recND = {"bias_logs": axis_a_dwell, "dc_map_detailed": mapC,
             "response_list": [filter_log("filter_added", "child_id")]}
    mND = dc_adapter.compute_dwell_metrics(mapC, axis_a_dwell)
    fND, rND, _ = llm_trigger.evaluate_trigger(recND, mND)
    print(f"    var_a axis + child_id filter (non-belief) -> fired={fND} reason={rND!r}")
    check("non-belief filter var alongside a valid belief var -> scopes to the valid "
          "one and FIRES (graceful drop, not scope_failed)",
          fND is True and rND == "ok")

    # --- filter-only on a non-belief var (TOTAL miss) -> scope_failed, no fire ----
    recNO = {"bias_logs": noaxis_dwell, "dc_map_detailed": mapC,
             "response_list": [filter_log("filter_added", "child_id")]}
    mNO = dc_adapter.compute_dwell_metrics(mapC, noaxis_dwell)
    fNO, rNO, tNO = llm_trigger.evaluate_trigger(recNO, mNO)
    check("filter-only on a non-belief var (total miss) -> scope_failed, no fire",
          fNO is False and rNO.startswith("scope_failed")
          and tNO["dwell_bias_percentile"] is None)

    print("\n" + "=" * 72)
    print(f"{'ALL CHECKS PASSED' if failures == 0 else str(failures) + ' CHECK(S) FAILED'}")
    print("=" * 72)
    return failures


if __name__ == "__main__":
    import sys
    sys.exit(1 if main() else 0)
