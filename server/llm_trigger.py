"""Trigger logic for the LLM intervention condition.

Two responsibilities live here, kept small so the socket layer never changes
when the trigger policy does:

1. evaluate_trigger -- the realtime dwell decision. Readiness gates (enough total
   dwell time and enough distinct teens) plus a recheck-spacing gate, then fire
   when the observed DwellBias sits at or above DWELL_PERCENTILE_THRESHOLD of its
   null distribution (dc_metric.dwell_bias_percentile). should_trigger is a thin
   bool wrapper over it for callers that don't need the reason.

2. evaluate_summary_trigger -- the pre-submission summary decision: the same
   percentile test scored on the final selection
   (dc_metric.selection_bias_percentile) rather than on dwell.

This module reads from dc_metric only; it does not modify it.
"""
import dc_metric


# --------------------------------------------------------------------------- #
# Realtime dwell gate. Readiness gates (enough attention to score at all) come
# first; the fire decision is the DwellBias percentile against its null
# distribution (dc_metric.dwell_bias_percentile), not a raw threshold.
# --------------------------------------------------------------------------- #
MIN_UNIQUE_HOVERS = 5              # distinct teens the participant lingered on
MIN_TOTAL_DWELL_SECONDS = 60.0     # total hover time before we score at all
DWELL_PERCENTILE_THRESHOLD = 0.95  # fire when DwellBias is at/above this percentile
DWELL_RECHECK_SECONDS = 30.0       # min extra dwell time between two percentile checks

# --------------------------------------------------------------------------- #
# Summary gate -- the pre-submission reflection, scored on the participant's
# FINAL SELECTION rather than on live dwell. Same percentile test
# (dc_metric.selection_bias_percentile); no cooldown/recheck (asked once at submit).
# --------------------------------------------------------------------------- #
MIN_SELECTIONS = 5                     # too few picks makes the mean DC meaningless
SELECTION_PERCENTILE_THRESHOLD = 0.95  # fire when SelectionBias is at/above this percentile

def evaluate_trigger(client_record, dwell_metrics):
    """Decide whether to fire a realtime intervention, AND say why not.

    Returns (fired, reason), where reason is a short code so a server log makes
    it obvious which condition blocked it:

        "ok" | "no_dwell_bias" | "not_ready" | "too_soon" | "below_percentile"

    Policy: enough attention to score (>= MIN_TOTAL_DWELL_SECONDS of dwell AND
    >= MIN_UNIQUE_HOVERS distinct teens), rechecked no more than once per
    DWELL_RECHECK_SECONDS of additional dwell, then fire when the observed
    DwellBias sits at or above DWELL_PERCENTILE_THRESHOLD of its null distribution
    (dc_metric.dwell_bias_percentile) and is positive.

    Side effect: records dwell_last_checked_seconds on client_record every time a
    percentile check is actually run (not only on fire), so rechecks are spaced by
    accumulated dwell. On fire, the caller (on_interaction) refreshes
    llm_last_fired_at.

    client_record: the CLIENTS[pid] dict (reads bias_logs / dc_map_detailed /
                   dwell_last_checked_seconds).
    dwell_metrics: the dict from dc_adapter.compute_dwell_metrics, i.e.
                   {"dwell_bias", "dwell_bias_v", "n_dwelled"}.
    """
    observed = dwell_metrics.get("dwell_bias")
    if observed is None:
        return False, "no_dwell_bias"

    dwell = dc_metric.dwell_by_teen(client_record.get("bias_logs", []))
    total_dwell_seconds = sum(dwell.values()) / 1000.0  # dwell_by_teen sums ms
    n_dwelled = dwell_metrics.get("n_dwelled", 0)

    # --- readiness: enough attention to score at all -------------------------
    if total_dwell_seconds < MIN_TOTAL_DWELL_SECONDS or n_dwelled < MIN_UNIQUE_HOVERS:
        return False, (f"not_ready ({total_dwell_seconds:.1f}s/{MIN_TOTAL_DWELL_SECONDS}s, "
                       f"{n_dwelled}/{MIN_UNIQUE_HOVERS} teens)")

    # --- recheck spacing: don't re-score on every interaction ----------------
    last_checked = client_record.get("dwell_last_checked_seconds")
    if last_checked is not None and total_dwell_seconds - last_checked < DWELL_RECHECK_SECONDS:
        return False, (f"too_soon ({total_dwell_seconds - last_checked:.1f}s < "
                       f"{DWELL_RECHECK_SECONDS}s of new dwell)")
    # A check is happening now; record it regardless of the fire outcome below.
    client_record["dwell_last_checked_seconds"] = total_dwell_seconds

    # --- C1: DwellBias percentile against its null distribution --------------
    pct = dc_metric.dwell_bias_percentile(client_record["dc_map_detailed"], dwell)
    if pct is None or pct < DWELL_PERCENTILE_THRESHOLD or observed <= 0:
        return False, f"below_percentile (pct={pct}, observed={observed:+.4f})"

    return True, "ok"


def should_trigger(client_record, dwell_metrics):
    """Whether to fire an intervention for this interaction (bool only).

    Thin wrapper over evaluate_trigger, kept so the swap-in point for the real
    percentile test has a stable, reason-free signature. Callers that want to
    log WHY it did not fire call evaluate_trigger directly.
    """
    fired, _reason = evaluate_trigger(client_record, dwell_metrics)
    return fired


def evaluate_summary_trigger(client_record, selection_metrics, selected_ids):
    """Decide whether to show the pre-submission summary, AND say why not.

    Sibling of evaluate_trigger with the same (fired, reason) contract, scored on
    the participant's final selection instead of their dwell:

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
    if pct is None or pct < SELECTION_PERCENTILE_THRESHOLD or observed <= 0:
        return False, f"below_percentile (pct={pct}, observed={observed:+.4f})"

    return True, "ok"
