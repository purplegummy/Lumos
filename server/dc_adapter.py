"""Adapter + integration glue between Prasit's stored priors and the DC metric.

This module is deliberately SEPARATE from the validated math in dc_metric.py.
dc_metric.py is frozen (its unit-tested functions must not change); everything
here is reshape/plumbing that can evolve without touching the math.

Prasit's merge stores priors as:

    CLIENTS[pid]["priors"]["{attribute}::{condition}"] = PriorBelief

where condition in {'diagnosed', 'not_diagnosed'} and each PriorBelief is one
group's distribution:

    { attribute, condition, binEdges, counts, ballCount, confidence,
      categories?  (present => categorical) }

dc_metric expects, per variable, ONE contract object:

    { attribute,
      binEdges | categories,                       # shared across the two groups
      countsByGroup: {
          diagnosed:    { counts, confidence },
          nonDiagnosed: { counts, confidence } } }

build_beliefs_from_priors() performs that reshape. See its docstring for the
completeness guard (d) and the shared-bins assertion (e).
"""
import dc_metric

# The diagnosis column is the y_d LABEL, never an elicited belief. Reuse the
# constant from dc_metric (do not duplicate the literal). Even if the modal
# elicits it, it must be excluded from beliefs and from the completeness count.
LABEL_ATTR = dc_metric.LABEL_ATTR

# The study elicits exactly six NON-LABEL variables; the DC map is only computed
# once all six have BOTH conditions. (Count-based so we don't hard-code the
# attribute names -- the elicitation UI defines which six, minus the label.)
EXPECTED_VARIABLE_COUNT = 6

# Prasit's condition strings -> the group keys dc_metric's contract uses.
CONDITION_TO_GROUP = {
    "diagnosed": "diagnosed",
    "not_diagnosed": "nonDiagnosed",
}


def _split_key(key):
    """'attribute::condition' -> (attribute, condition); tolerate a bare key."""
    if "::" in key:
        attr, cond = key.rsplit("::", 1)
        return attr, cond
    return key, "default"


def group_priors_by_attribute(priors):
    """{'attr::cond': belief, ...} -> {attr: {cond: belief, ...}}.

    Prefers the belief's own 'attribute'/'condition' fields; falls back to
    parsing the storage key.
    """
    grouped = {}
    for key, belief in priors.items():
        attr = belief.get("attribute") or _split_key(key)[0]
        cond = belief.get("condition") or _split_key(key)[1]
        grouped.setdefault(attr, {})[cond] = belief
    return grouped


def _shared_bins(attr, diagnosed_belief, non_belief):
    """Return the bins dict for the contract, asserting the two groups agree.

    Guard (e): both conditions are elicited over the FULL column, so their
    binEdges (numerical) or categories (categorical) must be identical. If a
    future binning change makes them diverge, fail LOUDLY here rather than
    silently bucketing the two groups differently.
    """
    d_is_cat = diagnosed_belief.get("categories") is not None
    n_is_cat = non_belief.get("categories") is not None
    if d_is_cat != n_is_cat:
        raise ValueError(
            f"[dc_adapter] '{attr}': one condition is categorical and the other "
            f"is numerical (diagnosed_cat={d_is_cat}, nonDiagnosed_cat={n_is_cat})"
        )

    if d_is_cat:
        d_cats = list(diagnosed_belief["categories"])
        n_cats = list(non_belief["categories"])
        if d_cats != n_cats:
            raise ValueError(
                f"[dc_adapter] '{attr}': categories differ between conditions "
                f"({d_cats} != {n_cats})"
            )
        return {"categories": d_cats}

    d_edges = [float(x) for x in diagnosed_belief.get("binEdges", [])]
    n_edges = [float(x) for x in non_belief.get("binEdges", [])]
    if d_edges != n_edges:
        raise ValueError(
            f"[dc_adapter] '{attr}': binEdges differ between conditions "
            f"({d_edges} != {n_edges})"
        )
    return {"binEdges": diagnosed_belief["binEdges"]}


def build_beliefs_from_priors(priors):
    """Reshape stored priors into dc_metric's contract.

    Returns (beliefs, report):
      beliefs: {attribute: contract_object} -- ONLY attributes that have BOTH
               conditions (guard d). Incomplete attributes are omitted.
      report:  {"complete": [...], "skipped_incomplete": [...]} for logging.

    Raises ValueError (guard e) if the two conditions of a complete attribute
    disagree on their bins/categories.
    """
    grouped = group_priors_by_attribute(priors)
    beliefs = {}
    skipped_incomplete = []

    for attr, by_cond in grouped.items():
        # The label column is the y_d target, never a belief variable. Drop it
        # before any completeness/countsByGroup handling so it can neither become
        # a belief nor be counted toward readiness or reported as incomplete.
        if attr == LABEL_ATTR:
            continue

        diagnosed = by_cond.get("diagnosed")
        non = by_cond.get("not_diagnosed")

        # Guard (d): both conditions required, else the variable is excluded.
        if diagnosed is None or non is None:
            skipped_incomplete.append(attr)
            continue

        bins = _shared_bins(attr, diagnosed, non)  # guard (e)

        beliefs[attr] = {
            "attribute": attr,
            **bins,
            "countsByGroup": {
                "diagnosed": {
                    "counts": diagnosed["counts"],
                    "confidence": diagnosed.get("confidence"),
                },
                "nonDiagnosed": {
                    "counts": non["counts"],
                    "confidence": non.get("confidence"),
                },
            },
        }

    report = {
        "complete": sorted(beliefs.keys()),
        "skipped_incomplete": sorted(skipped_incomplete),
    }
    return beliefs, report


def flatten_priors_for_bias(priors, condition="diagnosed"):
    """{'attr::cond': belief} -> {attr: belief}, for consumers that expect a
    single prior per attribute (bias.py's js_divergence) rather than
    dc_metric's countsByGroup-per-attribute contract.

    Picks `condition`'s belief per attribute (falling back to whichever
    condition is present, if only one has been committed so far -- bias
    metrics are computed incrementally as priors come in, before both
    conditions of every attribute are necessarily complete).
    """
    grouped = group_priors_by_attribute(priors)
    flat = {}
    for attr, by_cond in grouped.items():
        if attr == LABEL_ATTR:
            continue
        belief = by_cond.get(condition) or next(iter(by_cond.values()), None)
        if belief is not None:
            flat[attr] = belief
    return flat


def is_ready(priors, expected=EXPECTED_VARIABLE_COUNT):
    """True when at least `expected` variables have BOTH conditions elicited.

    Returns (ready, beliefs, report) so the caller doesn't rebuild twice.
    """
    beliefs, report = build_beliefs_from_priors(priors)
    return (len(beliefs) >= expected), beliefs, report


# --------------------------------------------------------------------------- #
# Interaction id extraction from bias_logs (the same list the JS metric reads).
# Each bias_logs entry is the raw frontend message; ids live at entry["data"]["id"]
# and are either a scalar teen id or a list (aggregate/group interaction).
# --------------------------------------------------------------------------- #
def _ids_of(entry):
    data = entry.get("data", {})
    if "id" not in data:
        return []
    val = data["id"]
    if isinstance(val, list):
        return [v for v in val if v is not None and v != "-"]
    if val is None or val == "-":
        return []
    return [val]


def interacted_ids(bias_logs):
    """De-duplicated, order-preserving union of every id touched in bias_logs."""
    seen = set()
    ordered = []
    for entry in bias_logs:
        for i in _ids_of(entry):
            if i not in seen:
                seen.add(i)
                ordered.append(i)
    return ordered


def current_interaction_ids(bias_logs):
    """Ids from just the most recent bias-relevant interaction (real-time set)."""
    if not bias_logs:
        return []
    return _ids_of(bias_logs[-1])


def selected_ids(bias_logs):
    """Reconstruct the current selection from add/remove events in bias_logs.

    Semantics (documented approximation): click_add_item selects, click_remove_item
    deselects, click_group toggles its whole group (deselect if all its ids are
    currently selected, else select). Hover events (mouseout_*) do not affect
    selection. This mirrors the frontend's selectedObjects behavior closely
    enough for selection_bias; it is derived purely from bias_logs (no extra
    state), so it stays consistent with what the JS metric sees.
    """
    selected = set()
    for entry in bias_logs:
        itype = entry.get("interactionType")
        ids = _ids_of(entry)
        if itype == "click_add_item":
            selected.update(ids)
        elif itype == "click_remove_item":
            selected.difference_update(ids)
        elif itype == "click_group":
            if ids and all(i in selected for i in ids):
                selected.difference_update(ids)
            else:
                selected.update(ids)
    return list(selected)


def compute_phase_metrics(dc_map, bias_logs):
    """Compute the DC phase metrics from a cached dc_map + bias_logs id sets.

    Reads the CACHED dc_map (never recomputes it). Returns a plain dict suitable
    for embedding in the interaction response's output_data.

      real_time_bias           : over the ids of the CURRENT interaction
      overall_interaction_bias : over ALL interacted ids so far
      selection_bias           : over the reconstructed current selection
      filter_bias              : None -- visible-before/after id sets are not
                                 available in bias_logs (filter interactions are
                                 not in COMPUTE_BIAS_FOR_TYPES), so it is skipped
                                 for now. See report note.
    """
    current = current_interaction_ids(bias_logs)
    every = interacted_ids(bias_logs)
    selected = selected_ids(bias_logs)
    return {
        "real_time_bias": dc_metric.real_time_bias(dc_map, current),
        "overall_interaction_bias": dc_metric.overall_interaction_bias(dc_map, every),
        "selection_bias": dc_metric.selection_bias(dc_map, selected),
        "filter_bias": None,  # not available from bias_logs; skipped this pass
        "n_current": len(current),
        "n_interacted": len(every),
        "n_selected": len(selected),
    }


def compute_dwell_metrics(detailed_map, bias_logs):
    """Compute the dwell-weighted bias metrics from a cached detailed dc_map.

    Sibling of compute_phase_metrics: it reads the CACHED dc_map_detailed (the
    per-teen {dc, consistency, weights} map, never recomputed) and the same
    bias_logs. dwell_by_teen sums interactionDuration over scalar mouseout_item
    entries; dwell_bias / dwell_bias_v then weight DC (point-level) and per-variable
    consistency (variable-level) by that dwell. dwell_bias_v stays RAW (weighted=False).

    Returns a plain dict for embedding in the interaction response's output_data:

      dwell_bias   : point-level, dwell-weighted mean DC minus the all-teen baseline
      dwell_bias_v : {variable: score}, per-variable dwell-weighted consistency bias
      n_dwelled    : number of distinct teens with any summed dwell

    NOTE: dwell_bias / dwell_bias_v raise fail-loud if a dwelled id is absent from
    the map. That strict guarantee is intentional and stays in dc_metric; the caller
    (on_interaction) guards this at the handler boundary so a stray id cannot break
    the live interaction response.
    """
    dwell = dc_metric.dwell_by_teen(bias_logs)
    return {
        "dwell_bias": dc_metric.dwell_bias(detailed_map, dwell),
        "dwell_bias_v": dc_metric.dwell_bias_v(detailed_map, dwell),
        "n_dwelled": len(dwell),
    }
