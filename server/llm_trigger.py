"""Trigger logic for the LLM intervention condition.

Two responsibilities live here, kept small so the socket layer never changes
when the trigger policy does:

1. evaluate_trigger -- the realtime dwell decision. A global readiness gate (enough
   total dwell time and enough distinct teens) plus a PER-VARIABLE recheck-spacing
   gate, then fire when the observed DwellBias sits at or above
   DWELL_PERCENTILE_THRESHOLD of its null distribution (dc_metric.
   dwell_bias_percentile). The DwellBias is SCOPED to the x/y variables the
   participant currently has on screen AND off cooldown -- dc_adapter re-pools DC
   over those (axes from llm_intervention.get_current_axes) before the same
   percentile test -- so the trigger reflects what is being looked at, not all six
   beliefs pooled, and one axis cooling down never blocks the other. should_trigger
   is a thin bool wrapper for callers that don't need the reason.

2. evaluate_summary_trigger -- the pre-submission summary decision: the same
   percentile test scored on the final selection
   (dc_metric.selection_bias_percentile) rather than on dwell.

3. evaluate_selection_progressive_trigger -- a NON-blocking, per-selection sibling of
   the realtime dwell trigger (1), scoring the running selection per variable and
   firing on the single most extreme one (Shiyao's max-across-variables rule). Built
   and unit-tested in isolation; NOT wired to any socket handler yet -- Sung's repeat
   cooldown is still pending, and without it firing on every selection past the 5th
   is unacceptable. Deliberately separate from evaluate_summary_trigger (2), which
   stays the submit-time summary path unchanged.

This module reads from dc_metric (scoring) plus dc_adapter (the scoped-map
reshape) and llm_intervention (get_current_axes); it modifies none of them.
"""
import dc_adapter
import dc_metric
import llm_intervention


# --------------------------------------------------------------------------- #
# Realtime dwell gate. Readiness gates (enough attention to score at all) come
# first; the fire decision is the DwellBias percentile against its null
# distribution (dc_metric.dwell_bias_percentile), not a raw threshold.
# --------------------------------------------------------------------------- #
MIN_UNIQUE_HOVERS = 5              # distinct teens the participant lingered on
MIN_TOTAL_DWELL_SECONDS = 20.0     # total hover time before we score at all
DWELL_PERCENTILE_THRESHOLD = 0.80  # fire when DwellBias is at/above this percentile
DWELL_RECHECK_SECONDS = 10.0       # min extra dwell between two checks of the SAME axis var

# --------------------------------------------------------------------------- #
# Summary gate -- the pre-submission reflection, scored on the participant's
# FINAL SELECTION rather than on live dwell. Same percentile test
# (dc_metric.selection_bias_percentile); no cooldown/recheck (asked once at submit).
# --------------------------------------------------------------------------- #
MIN_SELECTIONS = 5                     # too few picks makes the mean DC meaningless
SELECTION_PERCENTILE_THRESHOLD = 0.80  # fire when SelectionBias is at/above this percentile

def evaluate_trigger(client_record, dwell_metrics):
    """Decide whether to fire a realtime intervention, AND say why not.

    Returns (fired, reason, trace):
      * fired  -- bool.
      * reason -- a short code so a server log makes it obvious which condition
        blocked it: "ok" | "no_dwell_bias" | "not_ready" | "too_soon" |
        "no_visible_axes" | "scope_failed (...)" | "below_percentile".
      * trace  -- {"dwell_bias_percentile", "n_dwelled", "total_dwell_seconds"},
        the diagnostic values behind the decision (persisted on every call for
        trigger-policy analysis). dwell_bias_percentile here is scoped to the
        visible axis variables that are off cooldown, and is None whenever the
        gates stopped short of computing it (no axes yet, all visible vars still
        cooling, or an axis carrying a non-belief attribute).

    Policy: enough attention to score (>= MIN_TOTAL_DWELL_SECONDS of dwell AND
    >= MIN_UNIQUE_HOVERS distinct teens -- a GLOBAL, variable-agnostic readiness
    gate), then a PER-VARIABLE recheck gate: each currently-visible axis variable
    is rechecked no more than once per DWELL_RECHECK_SECONDS of additional (global)
    dwell since THAT variable was last checked, so one axis cooling down never
    blocks the other. The DwellBias is then SCOPED to the visible variables that
    are off cooldown and scored against its null (dc_metric.dwell_bias_percentile);
    it fires at or above DWELL_PERCENTILE_THRESHOLD. The raw score's sign is NOT
    gated: a high enough percentile fires even when DwellBias is negative (the
    positive-score requirement was removed in pilot round 2).

    Side effect: records dwell_last_checked_by_var[v] on client_record for each
    variable v actually rechecked this call (not only on fire), so rechecks are
    spaced per variable by accumulated dwell; and on fire records
    dwell_last_fired_vars (the checked vars) so reset_dwell_watermark rebases
    exactly them on dismiss. On fire, the caller (on_interaction) refreshes
    llm_last_fired_at.

    client_record: the CLIENTS[pid] dict (reads bias_logs / dc_map_detailed /
                   dwell_last_checked_by_var).
    dwell_metrics: the dict from dc_adapter.compute_dwell_metrics, i.e.
                   {"dwell_bias", "dwell_bias_v", "n_dwelled"}.
    """
    dwell = dc_metric.dwell_by_teen(client_record.get("bias_logs", []))
    total_dwell_seconds = sum(dwell.values()) / 1000.0  # dwell_by_teen sums ms
    n_dwelled = dwell_metrics.get("n_dwelled", 0)
    # Diagnostic trace persisted on every call; dwell_bias_percentile stays None
    # unless the gates below reach the (expensive) percentile computation.
    trace = {"dwell_bias_percentile": None,
             "n_dwelled": n_dwelled,
             "total_dwell_seconds": total_dwell_seconds}

    observed = dwell_metrics.get("dwell_bias")
    if observed is None:
        return False, "no_dwell_bias", trace

    # --- readiness: enough attention to score at all (GLOBAL, variable-agnostic)
    if total_dwell_seconds < MIN_TOTAL_DWELL_SECONDS or n_dwelled < MIN_UNIQUE_HOVERS:
        return False, (f"not_ready ({total_dwell_seconds:.1f}s/{MIN_TOTAL_DWELL_SECONDS}s, "
                       f"{n_dwelled}/{MIN_UNIQUE_HOVERS} teens)"), trace

    # --- resolve the visible axes ONCE: shared by the recheck gate and the
    # scoped percentile below (both need to know which variables are on screen).
    axes = llm_intervention.get_current_axes(client_record)
    visible_vars = list(dict.fromkeys(
        v for v in (axes.get("x"), axes.get("y")) if v is not None))
    if not visible_vars:
        return False, "no_visible_axes", trace

    # --- recheck spacing, PER VISIBLE VARIABLE -------------------------------
    # Each axis variable carries its own "last checked at" (in global pooled dwell
    # seconds); one cooling down does not block the other. Recheck whichever visible
    # variables have earned >= DWELL_RECHECK_SECONDS of new dwell since THEIR own
    # last check, and skip the rest.
    checked_at = client_record.setdefault("dwell_last_checked_by_var", {})
    ready_vars = [v for v in visible_vars
                  if total_dwell_seconds - checked_at.get(v, 0.0) >= DWELL_RECHECK_SECONDS]
    if not ready_vars:
        # None ready: report the one closest to ready (most new dwell so far).
        best = max(total_dwell_seconds - checked_at.get(v, 0.0) for v in visible_vars)
        return False, (f"too_soon ({best:.1f}s < {DWELL_RECHECK_SECONDS}s of new "
                       f"dwell for any visible var)"), trace

    # --- C1: DwellBias percentile against its null, SCOPED to the READY vars ---
    # Score on just the visible variables that are off cooldown (the pooled six-
    # belief DC is never used). A cooling variable is left out of the scope even
    # though it is on screen, so its attention neither earns nor blocks a fire.
    pct, scoped_observed, scope_reason = _scoped_dwell_percentile(
        client_record, dwell, ready_vars)
    trace["dwell_bias_percentile"] = pct
    if scope_reason is not None:
        return False, scope_reason, trace
    # A real check ran over ready_vars; advance THEIR clocks regardless of the fire
    # outcome below (the cooling vars' timestamps stay untouched).
    for v in ready_vars:
        checked_at[v] = total_dwell_seconds

    if pct is None or pct < DWELL_PERCENTILE_THRESHOLD:
        # observed is the SCOPED DwellBias (same ready-var scope as pct), not the
        # pooled dwell_metrics value, so the log names the score pct reflects.
        return False, f"below_percentile (pct={pct}, observed={scoped_observed:+.4f})", trace

    # Fired. Remember which variables this check covered so a later dismiss rebases
    # exactly their cooldowns (the visible axes may differ by dismiss time).
    client_record["dwell_last_fired_vars"] = list(ready_vars)
    return True, "ok", trace


def _scoped_dwell_percentile(client_record, dwell, scope_vars):
    """Scoped DwellBias percentile + its scoped observed value + a not-ready reason,
    computed over scope_vars only. -> (pct, observed, reason).

    (float|None, float, None)   -- the scoped percentile and the scoped DwellBias it
                                   was scored from (the re-pooled dwell_bias over
                                   scope_vars), so a below_percentile log names the
                                   score pct actually reflects. pct is None only for
                                   the empty-dwell k==0 case dc_metric guards, which
                                   the readiness gate rules out first.
    (None, None, "scope_failed (...)") -- scope_vars carry no belief variable, so
                                   scoped_detailed_map could not build a map. Caught
                                   here and treated as not-ready rather than
                                   propagated -- the same handler-boundary guard
                                   on_interaction uses for the dwell metrics.

    scope_vars is the caller's already-resolved list of visible variables that are
    off cooldown (Nones/dupes already dropped). Only the per-teen DC is re-pooled
    over them; the dwell weights (per teen) are untouched, so this stays the point-
    level DwellBias null test on a scoped score. The scoped map is built ONCE and
    feeds both outputs. rng is left default (global np.random), matching live.
    """
    try:
        scoped = dc_adapter.scoped_detailed_map(
            client_record["dc_map_detailed"], scope_vars)
    except Exception as e:
        print(f"[DWELL] scoped map failed: {e}", flush=True)
        return None, None, f"scope_failed ({e})"
    # One scoped map feeds both the null-distribution percentile and the observed
    # DwellBias it is scored against (dwell_bias is a cheap re-read, no sampling).
    pct = dc_metric.dwell_bias_percentile(scoped, dwell)
    observed = dc_metric.dwell_bias(scoped, dwell)
    return pct, observed, None


def should_trigger(client_record, dwell_metrics):
    """Whether to fire an intervention for this interaction (bool only).

    Thin wrapper over evaluate_trigger, kept so the swap-in point for the real
    percentile test has a stable, reason-free signature. Callers that want to
    log WHY it did not fire call evaluate_trigger directly.
    """
    fired, _reason, _trace = evaluate_trigger(client_record, dwell_metrics)
    return fired


def reset_dwell_watermark(client_record):
    """Rebase the recheck window for the variable(s) the dismissed intervention was
    scored on, from the dwell accumulated so far.

    Called when the participant's panel goes away (on_llm_dismissed). The spacing is
    measured in NEW hover time, so without this the seconds spent reading one
    intervention would count toward earning the next -- they would be paying for a
    reminder they were still looking at.

    Rebases ONLY the variables that were part of the check that fired the dismissed
    intervention (recorded as dwell_last_fired_vars when it fired), NOT whatever is
    on the axes at dismiss time -- the participant may have switched axes while the
    panel was up. Every other variable's clock is left exactly where it was. The
    fired-vars marker is consumed here so a stray repeat dismiss cannot re-rebase.
    """
    fired_vars = client_record.pop("dwell_last_fired_vars", None)
    if not fired_vars:
        return
    dwell = dc_metric.dwell_by_teen(client_record.get("bias_logs", []))
    now_seconds = sum(dwell.values()) / 1000.0
    checked_at = client_record.setdefault("dwell_last_checked_by_var", {})
    for v in fired_vars:
        checked_at[v] = now_seconds


def evaluate_summary_trigger(client_record, selection_metrics, selected_ids):
    """Decide whether to show the pre-submission summary, AND say why not.

    Returns (fired, reason) -- evaluate_trigger's first two, without the trace --
    scored on the participant's final selection instead of their dwell:

        "ok" | "gate_min_selections" | "below_percentile"

    Deliberately has NO cooldown and no recheck spacing: this is asked once, at
    the moment the participant tries to submit. "Once per session" is enforced
    CLIENT-side (llmSummaryRequested in main-activity/component.ts); this handler
    does not guard against repeats.

    The count is checked BEFORE the percentile: under MIN_SELECTIONS the mean DC
    is dominated by which few teens happened to be picked, so "below_percentile"
    would name the wrong cause.

    client_record:     the CLIENTS[pid] dict (reads the scalar dc_map).
    selection_metrics: the dict from llm_intervention.selection_metrics, i.e.
                       {"selection_bias", "selection_bias_v", "n_selected"}.
    selected_ids:      the participant's final selected teen ids.
    """
    n_selected = selection_metrics.get("n_selected", 0)
    if n_selected < MIN_SELECTIONS:
        return False, f"gate_min_selections ({n_selected} < {MIN_SELECTIONS})"

    observed = selection_metrics["selection_bias"]
    pct = dc_metric.selection_bias_percentile(client_record.get("dc_map"), selected_ids)
    if pct is None or pct < SELECTION_PERCENTILE_THRESHOLD:
        return False, f"below_percentile (pct={pct}, observed={observed:+.4f})"

    return True, "ok"


# --------------------------------------------------------------------------- #
# Progressive selection gate (BUILD-ONLY -- not wired live).
#
# A non-blocking, per-selection sibling of the realtime dwell trigger, scoring the
# running selection instead of dwell and PER VARIABLE instead of pooled, firing on
# the single most extreme variable (Shiyao's rule). It is intentionally NOT called
# from on_selected_subjects or any other socket handler yet: Sung's repeat cooldown
# is still pending, and without it this would fire on every selection past the 5th.
# The emission wrapper it hands off to (llm_intervention.generate_selection_and_emit)
# also exists but is likewise unwired.
#
# Deliberately separate from evaluate_summary_trigger above, which stays the
# submit-time summary path exactly as it is.
# --------------------------------------------------------------------------- #
def evaluate_selection_progressive_trigger(client_record, selected_ids):
    """Per-variable selection-bias fire decision for the running selection.

    Sibling of evaluate_trigger (the realtime dwell gate) in shape -- a readiness
    gate first, then the scoped scoring -- but scored on the SELECTION and across
    ALL belief variables, since selection has no axes to scope to. It does NOT
    modify or replace evaluate_summary_trigger; it is new, isolated logic.

    Returns a dict. Below readiness:
      {"ready": False, "reason": "not_ready (...)", "n_selected", "percentile_by_var": None}
    At/above readiness:
      {"ready":             True,
       "reason":            "ok",
       "fired":             bool,
       "target_var":        variable | None,   # the argmax variable, only on a fire
       "target_percentile": float | None,      # its percentile, only on a fire
       "n_selected":        int,                # unique selected ids
       "percentile_by_var": {variable: percentile}}   # full dict, for logging

    Reduction (Shiyao's rule): fire on the single MOST extreme variable. Take the
    max over the per-variable percentiles; if it is >= SELECTION_PERCENTILE_THRESHOLD
    the trigger fires with target_var = that argmax variable and target_percentile =
    its value. If nothing clears (or nothing scored), fired is False and target_var /
    target_percentile are None -- percentile_by_var is still returned in full so a
    log can show how close it got. Variables whose percentile is None (nothing
    selected present in the map -- uniform across variables) are excluded from the max.

    Readiness: n_selected >= MIN_SELECTIONS, the SAME constant and threshold the
    submit-time gate uses, which is what makes this "start at the 5th selection."

    client_record: the CLIENTS[pid] dict (reads dc_map_detailed).
    selected_ids:  the participant's currently-selected teen ids.
    """
    n_selected = len(set(selected_ids))
    if n_selected < MIN_SELECTIONS:
        return {"ready": False,
                "reason": f"not_ready ({n_selected} < {MIN_SELECTIONS} selections)",
                "n_selected": n_selected,
                "percentile_by_var": None}

    percentile_by_var = dc_adapter.selection_percentile_by_var(
        client_record["dc_map_detailed"], selected_ids)

    # Reduce across variables: fire on the single most extreme one. Only variables
    # with a computed percentile are eligible for the max (None = nothing selected
    # present in the map for that scope, uniform across variables).
    scored = {v: p for v, p in percentile_by_var.items() if p is not None}
    argmax_var = max(scored, key=scored.get) if scored else None
    max_pct = scored[argmax_var] if argmax_var is not None else None
    fired = max_pct is not None and max_pct >= SELECTION_PERCENTILE_THRESHOLD

    return {"ready": True,
            "reason": "ok",
            "fired": fired,
            # Name a target only on a fire; percentile_by_var carries the rest.
            "target_var": argmax_var if fired else None,
            "target_percentile": max_pct if fired else None,
            "n_selected": n_selected,
            "percentile_by_var": percentile_by_var}
