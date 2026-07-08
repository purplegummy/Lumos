"""Data-point confirmation-bias (DC) metric; offline first, online after elicitation
is finished

This is a standalone, pure-Python module. It is intentionally NOT wired into
the live request path (compute_metrics / server.py); this pass is the math plus
a validation harness (test_dc_metric.py) only.

It REUSES the existing JS primitives rather than reimplementing them:
  * js_divergence._normalize / _kl / EPSILON / _LN2  -- the JS divergence core,
    factored here into js_distance() (raw, un-inverted, sqrt -> a distance).
  * bias._bucket_numerical                            -- quantitative value -> bin
  * the categorical category-index idiom from bias.js_divergence (replicated as
    _bucket_categorical, the same {str(c): i} lookup).
  * active_data[pid][attr] resolution                 -- a teen id maps to its
    values; here a "teen" is the plain dict active_data[pid] would return, i.e.
    {attribute: value, ...} including the label column.

--------------------------------------------------------------------------------
THE METRIC (study definition)
--------------------------------------------------------------------------------
Per participant the elicitation emits, per variable, TWO balls-into-bins
distributions -- one for the "diagnosed" group, one for the "non-diagnosed"
group -- plus a confidence per group. The participant is, in effect, drawing
their belief about how each variable distributes differently between teens who
were vs. weren't diagnosed (ever_diagnosed_dep_or_anx).

For a teen t and a variable v:
  * Variable-bin association  A_v[b] = log((d[b]+alpha)/(n[b]+alpha))   (vba)
        d, n = the diagnosed / non-diagnosed elicited counts in bin b.
        A_v[b] > 0  => participant believes bin b leans "diagnosed".
        A_v[b] < 0  => participant believes bin b leans "non-diagnosed".
  * Consistency  C_v(t) = A_v[bin_of(t)] * y_d(t),
        y_d(t) = +1 if t is actually diagnosed ("Yes"), else -1.
        C_v(t) > 0  => teen t CONFIRMS the participant's believed association
                       (a believed-diagnosed bin holding an actually-diagnosed
                       teen, or a believed-non bin holding an actually-non teen).
  * Variable weight  w_v = js_distance(d, n)  in [0, 1].
        How strongly the participant separated the two groups for v. If they
        drew the two distributions identically, w_v = 0 and v drops out: a
        variable the participant has no differential belief about should not
        steer DC.
  * DC(t) = sum_v( w_v * C_v(t) ) / sum_v( w_v ).
        A confidence-weighted-by-conviction average consistency. DC(t) > 0 means
        teen t tends to confirm the participant's beliefs across the variables
        they actually hold a differential belief on.

The four phase metrics are all differences of subset means over the DC map:
interacting with high-DC (confirming) teens pushes the difference positive.

CONVENTIONS / CHOICES (flagged for review)
  * vba uses NATURAL log (np.log). The base only rescales A uniformly; natural
    log keeps it consistent with the nats used in the JS core. Change here if
    the analysis wants log2.
  * alpha = 0.5 additive smoothing in vba -- avoids
    log(0)/divide-by-zero for empty elicited bins and shrinks extreme A for
    sparsely-filled bins.
  * js_distance is the SQUARE ROOT of the normalized JS divergence (the proper
    metric form), NOT the inverted js_confirmation_score (1 - D/ln2). High =
    distributions differ; identical distributions -> 0.
  * real_time_bias and overall_interaction_bias / selection_bias use ALL 200
    teens as the baseline mean, even under active filters
"""
import math

import numpy as np

import bias_util
from bias import _bucket_numerical
from js_divergence import EPSILON, _LN2, _kl, _normalize

# Label column that defines a teen's actual diagnosed/non-diagnosed group.
LABEL_ATTR = "ever_diagnosed_dep_or_anx"
DIAGNOSED_VALUE = "Yes"

# Additive smoothing for the variable-bin association (vba)
DEFAULT_ALPHA = 0.5

# The single interaction type that carries a per-teen hover dwell we sum on.
# A completed point-hover emits mouseout_item with a top-level interactionDuration
# (ms) and a SCALAR data["id"]. (mouseover_item is not logged to bias_logs, and
# mouseout_group carries a LIST id -- see dwell_by_teen's scope note.)
MOUSEOUT_ITEM = "mouseout_item"


# --------------------------------------------------------------------------- #
# 1. js_distance -- the weight primitive (raw JS divergence, sqrt -> distance)
# --------------------------------------------------------------------------- #
def js_distance(counts_a, counts_b):
    """Jensen-Shannon DISTANCE between two count vectors over the same bins.

    Reuses the exact JS-divergence core from js_divergence.py (the same
    normalize -> epsilon-smooth -> m / KL / JS pipeline), rescales the raw
    divergence to [0, 1] by dividing by ln2, then takes the SQUARE ROOT to get
    the metric (distance) form.

    Unlike js_confirmation_score(), this is NOT inverted: identical
    distributions -> 0.0, maximally different -> 1.0.

    Args:
        counts_a, counts_b: non-negative count vectors of equal length.

    Returns:
        float in [0, 1]. 0.0 iff the two normalized (smoothed) distributions
        are identical.

    Raises:
        ValueError: vectors empty or of mismatched length.
    """
    p = np.asarray(counts_a, dtype=float)
    q = np.asarray(counts_b, dtype=float)
    if p.size == 0 or q.size == 0:
        raise ValueError("count vectors must be non-empty")
    if p.shape != q.shape:
        raise ValueError(
            f"bins must align: a has {p.size} bins, b has {q.size} bins"
        )

    # Same smoothing as js_confirmation_score: normalize, epsilon-smooth the
    # normalized vectors, renormalize so every bin is strictly positive.
    p = _normalize(p)
    q = _normalize(q)
    p = _normalize(p + EPSILON)
    q = _normalize(q + EPSILON)

    # Jensen-Shannon divergence (nats): D = 1/2 KL(p||m) + 1/2 KL(q||m).
    m = 0.5 * (p + q)
    divergence = 0.5 * _kl(p, m) + 0.5 * _kl(q, m)

    # Rescale [0, ln2] -> [0, 1] (clamp tiny FP excursions), then sqrt -> distance.
    normalized = divergence / _LN2
    normalized = min(1.0, max(0.0, normalized))
    return float(math.sqrt(normalized))


# --------------------------------------------------------------------------- #
# 2. vba -- variable-bin association
# --------------------------------------------------------------------------- #
def vba(counts_d, counts_n, alpha=DEFAULT_ALPHA):
    """Variable-bin association: A[b] = log((d[b]+alpha) / (n[b]+alpha)).

    Natural log (see module docstring). alpha=0.5 additive smoothing.

    Args:
        counts_d: diagnosed-group elicited counts, one per bin.
        counts_n: non-diagnosed-group elicited counts, same length/order.
        alpha:    additive smoothing constant.

    Returns:
        list[float], one association value per bin. >0 leans diagnosed,
        <0 leans non-diagnosed.

    Raises:
        ValueError: vectors empty or of mismatched length.
    """
    d = np.asarray(counts_d, dtype=float)
    n = np.asarray(counts_n, dtype=float)
    if d.size == 0 or n.size == 0:
        raise ValueError("count vectors must be non-empty")
    if d.shape != n.shape:
        raise ValueError(
            f"bins must align: diagnosed has {d.size} bins, "
            f"non-diagnosed has {n.size} bins"
        )
    return [float(math.log((d[b] + alpha) / (n[b] + alpha))) for b in range(d.size)]


# --------------------------------------------------------------------------- #
# Belief/teen helpers
# --------------------------------------------------------------------------- #
def _belief_list(beliefs):
    """Accept beliefs as either {attribute: belief} or [belief, ...] -> list."""
    if isinstance(beliefs, dict):
        return list(beliefs.values())
    return list(beliefs)


def _is_categorical(variable_belief):
    cats = variable_belief.get("categories")
    return cats is not None


def _bucket_categorical(categories, val):
    """Categorical value -> bin index, mirroring bias.js_divergence's idiom.

    Returns None when the value is not one of the elicited categories (caller
    decides what a non-matching value means).
    """
    cat_index = {str(c): i for i, c in enumerate(categories)}
    return cat_index.get(str(val))


def _variable_weight(variable_belief):
    """w_v = js_distance(diagnosed.counts, nonDiagnosed.counts)."""
    cbg = variable_belief["countsByGroup"]
    return js_distance(cbg["diagnosed"]["counts"], cbg["nonDiagnosed"]["counts"])


# --------------------------------------------------------------------------- #
# 3. consistency_for_teen
# --------------------------------------------------------------------------- #
def consistency_for_teen(teen, variable_belief):
    """C_v(t) for one teen and one variable belief.

    Buckets the teen's value into the variable's bins (numerical via
    _bucket_numerical over binEdges; categorical via category index over
    categories), reads A[bin] from vba(...), multiplies by y_d (+1 if the teen
    is diagnosed, else -1).

    Returns 0.0 if the teen's categorical value is not one of the elicited
    categories (no association can be assigned -- documented edge case).
    """
    attr = variable_belief["attribute"]
    raw = teen[attr]

    if _is_categorical(variable_belief):
        idx = _bucket_categorical(variable_belief["categories"], raw)
        if idx is None:
            return 0.0  # value outside the elicited category set -> no association
    else:
        idx = _bucket_numerical(variable_belief["binEdges"], bias_util.cast_to_num(raw))

    cbg = variable_belief["countsByGroup"]
    associations = vba(cbg["diagnosed"]["counts"], cbg["nonDiagnosed"]["counts"])
    y_d = 1.0 if str(teen[LABEL_ATTR]) == DIAGNOSED_VALUE else -1.0
    return associations[idx] * y_d


# --------------------------------------------------------------------------- #
# 4. dc_for_teen
# --------------------------------------------------------------------------- #
def _consistency_and_weights_for_teen(teen, beliefs):
    """Shared core: per-variable C_v and w_v for one teen, plus the DC value.

    dc_for_teen and dc_map_detailed both call this so the DC value is computed
    ONCE and exposed alongside its decomposition -- never recomputed. The DC math
    (weighted mean of C_v, with the sum_v(w_v)==0 -> 0.0 guard) is exactly what
    dc_for_teen used to inline; factoring it here does not change the result.

    Returns:
        (dc_value, consistency_by_var, weights_by_var) where the two dicts are
        keyed by variable attribute. w_v depends only on the belief (not the
        teen), so weights are identical across teens.
    """
    consistency = {}
    weights = {}
    numerator = 0.0
    denominator = 0.0
    for vb in _belief_list(beliefs):
        v = vb["attribute"]
        w = _variable_weight(vb)
        c = consistency_for_teen(teen, vb)
        consistency[v] = c
        weights[v] = w
        numerator += w * c
        denominator += w
    dc_value = 0.0 if denominator == 0.0 else numerator / denominator
    return dc_value, consistency, weights


def dc_for_teen(teen, beliefs):
    """DC(t) = sum_v(w_v * C_v) / sum_v(w_v).

    EDGE CASE: if sum_v(w_v) == 0 -- the participant drew the diagnosed and
    non-diagnosed distributions identically for EVERY variable, so there is no
    differential belief anywhere -- return 0.0 (documented; avoids 0/0).
    """
    dc_value, _consistency, _weights = _consistency_and_weights_for_teen(teen, beliefs)
    return dc_value


# --------------------------------------------------------------------------- #
# 5. dc_map
# --------------------------------------------------------------------------- #
def dc_map(teens, beliefs):
    """Compute DC(t) for every teen once.

    Args:
        teens: dict {teen_id: {attribute: value, ...}}, the active_data idiom
            (each teen dict must include the LABEL_ATTR column).
        beliefs: the participant's beliefs ({attribute: belief} or list).

    Returns:
        dict {teen_id: dc_value}.
    """
    return {tid: dc_for_teen(teens[tid], beliefs) for tid in teens}


def dc_map_detailed(teens, beliefs):
    """Like dc_map, but each entry retains the per-variable decomposition.

    Returns:
        dict {teen_id: {
            "dc":          float,               # identical to dc_map[teen_id]
            "consistency": {variable: C_v},     # per-variable belief-consistency
            "weights":     {variable: w_v}}}    # per-variable js weight (teen-independent)

    dc_map (scalar per teen) is intentionally left UNCHANGED -- the frozen phase
    metrics and the live adapter consume DC as a bare number. This richer map is
    what the dwell metrics decompose. Storing weights too makes the raw-vs-weighted
    variable-consistency choice in dwell_bias_v a one-line switch.
    """
    out = {}
    for tid in teens:
        dc_value, consistency, weights = _consistency_and_weights_for_teen(
            teens[tid], beliefs
        )
        out[tid] = {"dc": dc_value, "consistency": consistency, "weights": weights}
    return out


# --------------------------------------------------------------------------- #
# Subset-mean helpers for the phase metrics
# --------------------------------------------------------------------------- #
def _mean_over(dcs, ids):
    """Mean DC over a set of ids; None if the (deduped, present) subset is empty."""
    seen = set()
    vals = []
    for i in ids:
        if i in dcs and i not in seen:
            seen.add(i)
            vals.append(dcs[i])
    if not vals:
        return None
    return sum(vals) / len(vals)


def _mean_all(dcs):
    """Baseline mean DC over ALL teens; None if the map is empty."""
    if not dcs:
        return None
    return sum(dcs.values()) / len(dcs)


def _mean_all_dc(detailed_map):
    """Baseline mean DC over ALL teens of a dc_map_detailed; None if empty."""
    if not detailed_map:
        return None
    return sum(e["dc"] for e in detailed_map.values()) / len(detailed_map)


# --------------------------------------------------------------------------- #
# 6. The four phase metrics (differences of subset means over dc_map)
# --------------------------------------------------------------------------- #
def real_time_bias(dcs, interacted_ids):
    """mean(DC over interacted) - mean(DC over ALL teens).

    Baseline is ALL teens even when filters are active. The
    comparison is always against the full population, so filtering does not
    move the goalposts. Returns 0.0 if no interactions (documented).
    """
    sub = _mean_over(dcs, interacted_ids)
    base = _mean_all(dcs)
    if sub is None or base is None:
        return 0.0
    return sub - base


def filter_bias(dcs, visible_before_ids, visible_after_ids):
    """mean(DC over visible_after) - mean(DC over visible_before).

    How a filter change shifted the mean DC of the *visible* set. Returns 0.0
    if either visible subset is empty (documented).
    """
    before = _mean_over(dcs, visible_before_ids)
    after = _mean_over(dcs, visible_after_ids)
    if before is None or after is None:
        return 0.0
    return after - before


def overall_interaction_bias(dcs, all_interacted_ids):
    """mean(DC over all interacted) - mean(DC over ALL teens).

    Same shape as real_time_bias but over the whole session's interactions.
    Returns 0.0 if nothing was interacted with (documented).
    """
    sub = _mean_over(dcs, all_interacted_ids)
    base = _mean_all(dcs)
    if sub is None or base is None:
        return 0.0
    return sub - base


def selection_bias(dcs, selected_ids):
    """mean(DC over selected) - mean(DC over ALL teens).

    Returns 0.0 if nothing was selected (documented).
    """
    sub = _mean_over(dcs, selected_ids)
    base = _mean_all(dcs)
    if sub is None or base is None:
        return 0.0
    return sub - base


# --------------------------------------------------------------------------- #
# 7. Dwell (attention-time) helpers + the two dwell-weighted bias metrics
# --------------------------------------------------------------------------- #
def dwell_by_teen(logs):
    """Sum per-teen hover dwell (ms) from bias_logs -> {teen_id: total_dwell_ms}.

    Filters to mouseout_item entries whose data["id"] is a SCALAR teen id, groups
    by id, and sums the top-level interactionDuration (ms). Repeated hovers on the
    same teen accumulate (each hover is a separate log entry).

    SCOPE (this pass): scalar mouseout_item ONLY. A mouseout_group entry carries a
    LIST id and a single aggregate duration; attributing that group duration to its
    member teens is an open modeling decision (equal split? full to each? weight by
    something?), so group hovers are deliberately skipped here rather than guessed.
    TODO(group-hover): decide member attribution before folding mouseout_group in.

    Args:
        logs: the bias_logs list (each entry is the raw frontend message).

    Returns:
        dict {teen_id: total_dwell_ms} (float ms). Ids with no dwell are absent.
    """
    dwell = {}
    for entry in logs:
        if entry.get("interactionType") != MOUSEOUT_ITEM:
            continue
        data = entry.get("data", {})
        tid = data.get("id")
        # scalar ids only: skip list (group) ids and the "-" / None placeholders.
        if isinstance(tid, list) or tid is None or tid == "-":
            continue
        duration = entry.get("interactionDuration", 0) or 0
        dwell[tid] = dwell.get(tid, 0.0) + float(duration)
    return dwell


def dwell_bias(detailed_map, dwell):
    """DwellBias (point-level): dwell-weighted mean DC minus the all-teen mean DC.

        DwellBias = sum_i(dwell_i * DC_i) / sum_i(dwell_i)  -  mean(DC over ALL teens)

    where i ranges over teens with dwell_i > 0. The baseline is the FIXED all-200
    mean DC (same convention as the phase metrics). Positive => the participant
    spent their attention disproportionately on belief-confirming (high-DC) teens.

    FAIL-LOUD: a dwelled id absent from detailed_map raises KeyError -- every teen
    is in the map, so a miss is a real bug, not a droppable event (project's
    fail-loud-over-silent-repair convention).

    Guard: sum(dwell) == 0 (no positive-dwell teens) -> 0.0, mirroring the phase
    metrics' empty-input return.
    """
    numerator = 0.0
    denominator = 0.0
    for tid, d in dwell.items():
        if d <= 0:
            continue
        if tid not in detailed_map:
            raise KeyError(f"[dwell_bias] dwelled id {tid!r} not in dc_map")
        numerator += d * detailed_map[tid]["dc"]
        denominator += d
    if denominator == 0.0:
        return 0.0
    base = _mean_all_dc(detailed_map)
    if base is None:
        return 0.0
    return numerator / denominator - base


def dwell_bias_v(detailed_map, dwell, weighted=False):
    """DwellBias_v (variable-level) -> {variable: score}.

        DwellBias_v[v] = sum_i(dwell_i * VC_{i,v}) / sum_i(dwell_i)
                         - mean(VC_{i,v} over ALL teens)

    where i ranges over teens with dwell_i > 0 and VC_{i,v} is teen i's per-variable
    consistency stored in detailed_map. Same dwell weights and same fixed all-200
    baseline (computed per variable) as the point-level DwellBias.

    raw-vs-weighted VC (FLAGGED for confirmation): by default VC_{i,v} is the RAW
    per-variable consistency C_{i,v} ("belief-consistency of a variable in a data
    point"). Pass weighted=True to use the js-weighted contribution w_v * C_{i,v}
    instead -- the stored weights make this a one-line switch. Because w_v is
    teen-independent, with weighted=True the per-variable scores decompose the
    point-level DwellBias exactly: sum_v DwellBias_v[v] / sum_v(w_v) == DwellBias.

    FAIL-LOUD: a dwelled id absent from detailed_map raises KeyError.

    Guard: sum(dwell) == 0 -> every variable scores 0.0. Empty map -> {}.
    """
    if not detailed_map:
        return {}
    variables = list(next(iter(detailed_map.values()))["consistency"].keys())

    def vc(entry, v):
        c = entry["consistency"][v]
        return entry["weights"][v] * c if weighted else c

    n = len(detailed_map)
    baseline = {
        v: sum(vc(e, v) for e in detailed_map.values()) / n for v in variables
    }

    numerator = {v: 0.0 for v in variables}
    denominator = 0.0
    for tid, d in dwell.items():
        if d <= 0:
            continue
        if tid not in detailed_map:
            raise KeyError(f"[dwell_bias_v] dwelled id {tid!r} not in dc_map")
        entry = detailed_map[tid]
        denominator += d
        for v in variables:
            numerator[v] += d * vc(entry, v)
    if denominator == 0.0:
        return {v: 0.0 for v in variables}
    return {v: numerator[v] / denominator - baseline[v] for v in variables}