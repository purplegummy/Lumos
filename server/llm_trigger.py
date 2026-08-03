"""Trigger logic for the LLM intervention condition.

Two responsibilities live here, deliberately kept small and swappable so the
real statistical test can drop in later without touching the socket layer:

1. derive_attended_direction -- implemented for real. Given the teens, the
   per-teen dwell times, and the belief variables, decide for each variable
   whether the participant lingered on HIGHER or LOWER values than the group as
   a whole (for the one ordinal/categorical variable, MORE / LESS difficulty).

2. should_trigger -- a STUB. The agreed design (07/08 meeting) is a
   null-distribution permutation test, not a fixed threshold; that is owned by
   Lester and is not committed upstream yet. For now this is a raw-threshold
   gate plus the three session gates plus a cooldown, structured so swapping in
   the permutation test replaces only this one function body.

This module reads from dc_metric / bias_util only; it does not modify them.
"""
import bias_util
import dc_metric


# --------------------------------------------------------------------------- #
# Constants -- PLACEHOLDERS. Lester/Prasit set the real values.
# --------------------------------------------------------------------------- #
# TODO(lester): replace evaluate_trigger's threshold branch with the agreed
# null-distribution permutation test: resample N teens at random from all 200,
# randomly assign the dwell times, recompute DC, repeat ~500x to build a null
# distribution, and fire only when the observed DwellBias sits above the
# 90th/95th percentile. Until then this is a raw-threshold placeholder.
DWELL_BIAS_THRESHOLD = 0.15      # observed point-level DwellBias must exceed this

# The three gate conditions -- stop the metric firing on noise early on.
MIN_UNIQUE_HOVERS = 5           # distinct teens the participant lingered on
MIN_TOTAL_DWELL_SECONDS = 10.0  # total time spent hovering data points
MIN_MINUTES_IN_TASK = 1.0       # wall-clock minutes since the participant connected

# Interventions must not fire repeatedly while someone sits above threshold.
COOLDOWN_SECONDS = 90.0

# Ordinal encoding for the one categorical belief variable, so a "direction"
# (more vs. less difficulty) can be derived the same way as for numeric ones.
CATEGORY_ORDINALS = {
    "difficulty_making_friends": {
        "No difficulty": 0,
        "A little difficulty": 1,
        "A lot of difficulty": 2,
    },
}


def _variable_value(teen, variable):
    """Numeric (or ordinal-encoded) value of a teen's variable, or None.

    Categorical belief variables are mapped through CATEGORY_ORDINALS; numeric
    ones are cast to float. Anything unrecognised returns None and is skipped by
    the caller (so a stray column can't crash direction derivation).
    """
    if variable not in teen:
        return None
    raw = teen[variable]
    if variable in CATEGORY_ORDINALS:
        return CATEGORY_ORDINALS[variable].get(str(raw))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def derive_attended_direction(teens, dwell, variables):
    """For each variable, which end the participant's attention skewed toward.

    Compares the dwell-weighted mean of the dwelled teens' values against the
    unweighted all-teen mean. Above (or equal to) the group mean -> "higher"
    ("more_difficulty" for the categorical variable); below -> "lower"
    ("less_difficulty"). Variables with no usable values or no dwell are omitted.

    teens:     {teen_id: {attr: value}} from bias.DATA_MAP[app_mode]["data"]
    dwell:     {teen_id: total_dwell_ms} from dc_metric.dwell_by_teen(...)
    variables: iterable of belief-variable names to score.
    Returns {variable: direction}.
    """
    result = {}
    for variable in variables:
        all_values = []
        weighted_sum = 0.0
        total_weight = 0.0
        for teen_id, teen in teens.items():
            value = _variable_value(teen, variable)
            if value is None:
                continue
            all_values.append(value)
            weight = dwell.get(teen_id, 0.0)
            if weight > 0:
                weighted_sum += weight * value
                total_weight += weight
        if not all_values or total_weight == 0:
            continue
        all_mean = sum(all_values) / len(all_values)
        dwell_mean = weighted_sum / total_weight
        skewed_higher = dwell_mean >= all_mean
        if variable in CATEGORY_ORDINALS:
            result[variable] = "more_difficulty" if skewed_higher else "less_difficulty"
        else:
            result[variable] = "higher" if skewed_higher else "lower"
    return result


def evaluate_trigger(client_record, dwell_metrics):
    """Decide whether to fire an intervention, AND say why not. STUB.

    Single source of truth for the trigger decision. Returns (fired, reason),
    where reason is a short gate-specific code so a server log makes it obvious
    which constant to tune while exploring:

        "ok" | "no_dwell_bias" | "below_threshold" | "gate_unique_hovers"
        | "gate_dwell_seconds" | "gate_time_in_task" | "cooldown"

    Each non-"ok" reason also carries the observed vs. required values.

    Real design is the null-distribution permutation test (TODO(lester) below);
    for now: observed DwellBias over DWELL_BIAS_THRESHOLD, AND all three session
    gates met, AND not within COOLDOWN_SECONDS of the last fire.

    client_record: the CLIENTS[pid] dict (reads bias_logs / connected_at /
                   llm_last_fired_at).
    dwell_metrics: the dict from dc_adapter.compute_dwell_metrics, i.e.
                   {"dwell_bias", "dwell_bias_v", "n_dwelled"}.
    """
    observed = dwell_metrics.get("dwell_bias")
    if observed is None:
        return False, "no_dwell_bias"

    # --- gate 1: threshold ---------------------------------------------------
    # TODO(lester): this is the branch the null-distribution permutation test
    # replaces -- resample N teens at random from all 200, randomly assign the
    # dwell times, recompute DC, repeat ~500x, and fire only when the observed
    # DwellBias sits above the 90th/95th percentile. Only this branch changes;
    # the gates and cooldown below stay as they are.
    if observed <= DWELL_BIAS_THRESHOLD:
        return False, f"below_threshold ({observed:+.4f} <= {DWELL_BIAS_THRESHOLD})"

    # --- gate 2: minimum unique hovers ---------------------------------------
    n_dwelled = dwell_metrics.get("n_dwelled", 0)
    if n_dwelled < MIN_UNIQUE_HOVERS:
        return False, f"gate_unique_hovers ({n_dwelled} < {MIN_UNIQUE_HOVERS})"

    # --- gate 3: minimum total dwell seconds ---------------------------------
    dwell = dc_metric.dwell_by_teen(client_record.get("bias_logs", []))
    total_dwell_seconds = sum(dwell.values()) / 1000.0  # dwell_by_teen sums ms
    if total_dwell_seconds < MIN_TOTAL_DWELL_SECONDS:
        return False, (f"gate_dwell_seconds ({total_dwell_seconds:.1f}s < "
                       f"{MIN_TOTAL_DWELL_SECONDS}s)")

    # --- gate 4: minimum minutes in task -------------------------------------
    now = bias_util.get_current_time()  # millis
    connected_at = client_record.get("connected_at", now)
    minutes_in_task = (now - connected_at) / 60000.0
    if minutes_in_task < MIN_MINUTES_IN_TASK:
        return False, (f"gate_time_in_task ({minutes_in_task:.1f}min < "
                       f"{MIN_MINUTES_IN_TASK}min)")

    # --- cooldown ------------------------------------------------------------
    last_fired = client_record.get("llm_last_fired_at", 0)
    seconds_since = (now - last_fired) / 1000.0
    if seconds_since < COOLDOWN_SECONDS:
        return False, f"cooldown ({COOLDOWN_SECONDS - seconds_since:.0f}s remaining)"

    return True, "ok"


def should_trigger(client_record, dwell_metrics):
    """Whether to fire an intervention for this interaction (bool only).

    Thin wrapper over evaluate_trigger, kept so the swap-in point for the real
    percentile test has a stable, reason-free signature. Callers that want to
    log WHY it did not fire call evaluate_trigger directly.
    """
    fired, _reason = evaluate_trigger(client_record, dwell_metrics)
    return fired
