"""Trigger logic for the LLM intervention condition.

Three responsibilities live here, kept small so the socket layer never changes
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

2. evaluate_selection_trigger -- the live mid-task decision: the progressive trigger
   (3) held to a fixed pick schedule (a check at the 5th selection and every
   SELECTION_RECHECK_PICKS after: 5th, 7th, 9th), whether or not an earlier check
   fired.

3. evaluate_selection_progressive_trigger -- a per-selection sibling of the realtime
   dwell trigger (1), scoring the running selection per variable and firing on the
   single most extreme one (Shiyao's max-across-variables rule). The scoring is
   SCOPED to the currently-active variables (x/y axes + active filters, via
   llm_intervention.get_currently_active_variables) -- the same active set the dwell
   trigger uses -- so a variable the participant is not looking at or filtering on
   neither earns nor blocks a fire. Reached live only through evaluate_selection_trigger (2).

This module reads from dc_metric (scoring) plus dc_adapter (the scoped-map reshape)
and llm_intervention (get_current_axes / get_currently_active_variables); it modifies
none of them.
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
SYSTEM_FIRE_COOLDOWN_MS = 30_000   # min REAL (wall-clock) time between any two fired
                                   # interventions, layered on top of the per-variable
                                   # recheck spacing above (Shiyao's system-wide cooldown)

# --------------------------------------------------------------------------- #
# Selection gate -- scored on the participant's RUNNING SELECTION rather than on
# live dwell. Same percentile test (dc_metric.selection_bias_percentile), applied
# per variable by the progressive trigger below.
# --------------------------------------------------------------------------- #
MIN_SELECTIONS = 5                     # too few picks makes the mean DC meaningless
SELECTION_PERCENTILE_THRESHOLD = 0.80  # fire when SelectionBias is at/above this percentile
SELECTION_RECHECK_PICKS = 2            # picks between two checks (5th, 7th, 9th ...)

def evaluate_trigger(client_record, dwell_metrics, now_ms=None):
    """Decide whether to fire a realtime intervention, AND say why not.

    Returns (fired, reason, trace):
      * fired  -- bool.
      * reason -- a short code so a server log makes it obvious which condition
        blocked it: "ok" | "no_dwell_bias" | "not_ready" | "system_cooldown (...)" |
        "too_soon" | "no_visible_axes" | "scope_failed (...)" | "below_percentile".
      * trace  -- {"dwell_bias_percentile", "n_dwelled", "total_dwell_seconds"},
        the diagnostic values behind the decision (persisted on every call for
        trigger-policy analysis). dwell_bias_percentile here is scoped to the
        visible axis variables that are off cooldown, and is None whenever the
        gates stopped short of computing it (no axes yet, all visible vars still
        cooling, or an axis carrying a non-belief attribute).

    Policy: enough attention to score (>= MIN_TOTAL_DWELL_SECONDS of dwell AND
    >= MIN_UNIQUE_HOVERS distinct teens -- a GLOBAL, variable-agnostic readiness
    gate), then a SYSTEM-WIDE wall-clock cooldown (>= SYSTEM_FIRE_COOLDOWN_MS of REAL
    time since the last fire), then a PER-VARIABLE recheck gate: each currently-visible
    axis variable is rechecked no more than once per DWELL_RECHECK_SECONDS of additional
    (global) dwell since THAT variable was last checked, so one axis cooling down never
    blocks the other. The DwellBias is then SCOPED to the visible variables that
    are off cooldown and scored against its null (dc_metric.dwell_bias_percentile);
    it fires at or above DWELL_PERCENTILE_THRESHOLD. The raw score's sign is NOT
    gated: a high enough percentile fires even when DwellBias is negative (the
    positive-score requirement was removed in pilot round 2).

    System cooldown vs per-variable spacing: these are two DIFFERENT clocks, layered.
    The per-variable gate below counts ACCUMULATED DWELL (only advances while hovering);
    this system gate counts REAL elapsed time (now_ms - llm_last_fired_at), so it also
    covers the participant reading the panel. It is placed BEFORE any scoping/scoring so
    a suppressed check does no work and -- critically -- mutates NO cooldown state:
    dwell_last_checked_by_var and dwell_last_fired_vars are left exactly as if scoring
    never ran, so nothing "earns" toward the next fire while cooling.

    Side effect: records dwell_last_checked_by_var[v] on client_record for each
    variable v actually rechecked this call (not only on fire), so rechecks are
    spaced per variable by accumulated dwell; and on fire records
    dwell_last_fired_vars (the checked vars) so reset_dwell_watermark rebases
    exactly them on dismiss. On fire, the caller (on_interaction) refreshes
    llm_last_fired_at.

    client_record: the CLIENTS[pid] dict (reads bias_logs / dc_map_detailed /
                   dwell_last_checked_by_var / llm_last_fired_at).
    dwell_metrics: the dict from dc_adapter.compute_dwell_metrics, i.e.
                   {"dwell_bias", "dwell_bias_v", "n_dwelled"}.
    now_ms:        current wall-clock time in epoch ms (bias_util.get_current_time()'s
                   unit), passed in by the caller so this module imports no clock and
                   its tests stay deterministic. None (no clock supplied) skips the
                   system-cooldown gate entirely -- the live path always passes it.
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

    # --- SYSTEM-WIDE wall-clock cooldown: no two fires within SYSTEM_FIRE_COOLDOWN_MS
    # of REAL time. Placed here (after readiness, before ANY scoping/scoring) so a
    # suppressed check does no work and touches NO cooldown state -- dwell_last_checked_
    # by_var / dwell_last_fired_vars stay exactly as if scoring never ran. Skipped when
    # no clock is supplied (now_ms is None) or there has been no fire yet this session.
    # The boundary matches DWELL_RECHECK's ">= is ready": elapsed < cooldown suppresses,
    # so at EXACTLY SYSTEM_FIRE_COOLDOWN_MS it is allowed through.
    last_fired = client_record.get("llm_last_fired_at")
    if now_ms is not None and last_fired is not None:
        elapsed_ms = now_ms - last_fired
        if elapsed_ms < SYSTEM_FIRE_COOLDOWN_MS:
            return False, (f"system_cooldown ({elapsed_ms / 1000.0:.1f}s < "
                           f"{SYSTEM_FIRE_COOLDOWN_MS / 1000.0:.0f}s since last fire)"), trace

    # --- resolve the CURRENTLY ACTIVE variables ONCE: the x/y axis attributes PLUS
    # any attribute with an active filter (Shiyao's request), shared by the recheck
    # gate and the scoped percentile below. sorted() only for a deterministic order
    # (the source is a set); order does not affect scoring or cooldown, which key on
    # the variable-name strings.
    visible_vars = sorted(
        llm_intervention.get_currently_active_variables(client_record))
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
    # A FRESH seeded generator for this evaluation's one null-sampling call, so the
    # percentile is reproducible from its inputs alone (default_rng(SEED) is identical
    # regardless of when in the call it is built -- it consumes no prior state).
    pct, scoped_observed, scope_reason = _scoped_dwell_percentile(
        client_record, dwell, ready_vars, dc_adapter.live_rng())
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


def _scoped_dwell_percentile(client_record, dwell, scope_vars, rng=None):
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
    feeds both outputs. rng is the seeded generator the caller builds per evaluation
    (dc_adapter.live_rng()) so the null draw is reproducible; None falls back to the
    global np.random state (used by unseeded unit tests).
    """
    try:
        scoped = dc_adapter.scoped_detailed_map(
            client_record["dc_map_detailed"], scope_vars)
    except Exception as e:
        print(f"[DWELL] scoped map failed: {e}", flush=True)
        return None, None, f"scope_failed ({e})"
    # One scoped map feeds both the null-distribution percentile and the observed
    # DwellBias it is scored against (dwell_bias is a cheap re-read, no sampling).
    pct = dc_metric.dwell_bias_percentile(scoped, dwell, rng=rng)
    observed = dc_metric.dwell_bias(scoped, dwell)
    return pct, observed, None


def should_trigger(client_record, dwell_metrics, now_ms=None):
    """Whether to fire an intervention for this interaction (bool only).

    Thin wrapper over evaluate_trigger, kept so the swap-in point for the real
    percentile test has a stable, reason-free signature. Callers that want to
    log WHY it did not fire call evaluate_trigger directly. now_ms is threaded
    through so this wrapper honours the same system-wide cooldown.
    """
    fired, _reason, _trace = evaluate_trigger(client_record, dwell_metrics, now_ms)
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


def evaluate_selection_trigger(client_record, selected_ids):
    """The live mid-task decision: the progressive trigger on a fixed pick schedule.

    Thin wrapper over evaluate_selection_progressive_trigger (below), which owns
    the readiness gate and the per-variable fire decision. This adds the schedule:
    a check runs at MIN_SELECTIONS and then every SELECTION_RECHECK_PICKS picks
    after it (the 5th, 7th and 9th selections), whether or not an earlier check
    fired. Two additional picks are the unit of NEW EVIDENCE between checks, not
    a post-fire penalty -- checking one pick after a non-fire would re-ask the
    same question of nearly the same selection.

    Each checkpoint fires AT MOST ONCE per session (Shiyao: "we just check their
    selections once at 5, once at 7, and once at 9"), tracked as the set of
    consumed checkpoints (selection_checkpoints_checked): deselecting below one
    and re-selecting back to it does not re-run it, because reaching the same
    count again is a reshuffle of an already-checked selection, not two
    selections of new evidence.

    Returns the progressive trigger's dict unchanged; on a skipped check, the
    same shape as its not-ready case with an "off_schedule (...)" or
    "already_checked (...)" reason.
    """
    n_selected = len(set(selected_ids))
    if n_selected >= MIN_SELECTIONS:
        past = (n_selected - MIN_SELECTIONS) % SELECTION_RECHECK_PICKS
        if past != 0:
            return {"ready": False,
                    "reason": (f"off_schedule (n={n_selected}, next check at "
                               f"{n_selected + SELECTION_RECHECK_PICKS - past})"),
                    "n_selected": n_selected,
                    "percentile_by_var": None}
        checked = client_record.setdefault("selection_checkpoints_checked", set())
        if n_selected in checked:
            return {"ready": False,
                    "reason": f"already_checked (checkpoint {n_selected} consumed)",
                    "n_selected": n_selected,
                    "percentile_by_var": None}
        checked.add(n_selected)

    return evaluate_selection_progressive_trigger(client_record, selected_ids)


def _confidence_for_var(client_record, var):
    """The elicited confidence (1-100) for a belief variable, for the priority tiebreak.

    Read straight from the cached, reshaped beliefs
    (client_record["beliefs"][var]["countsByGroup"]["diagnosed"]["confidence"]);
    beliefs is only ever populated alongside dc_map_detailed (server.maybe_compute_dc_map),
    and is already scoped to the six complete belief variables -- exactly the pool a
    candidate variable comes from -- so this is a safe direct read. Both conditions
    carry the SAME slider value (one confidence per variable), so "diagnosed" is
    representative.

    Missing/None (legacy priors saved before the confidence step, or beliefs not yet
    built) -> 0, a sentinel below the real 1-100 range so the variable sorts LAST
    within its tier without being dropped from candidacy. Never raises.
    """
    try:
        conf = (client_record.get("beliefs", {})[var]
                ["countsByGroup"]["diagnosed"]["confidence"])
        return 0.0 if conf is None else float(conf)
    except (KeyError, TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------- #
# Progressive selection gate.
#
# A non-blocking, per-selection sibling of the realtime dwell trigger, scoring the
# running selection instead of dwell and PER VARIABLE instead of pooled, firing on
# the single most extreme variable (Shiyao's rule). Reached live only through
# evaluate_selection_trigger above, whose pick-counted cooldown keeps it from
# firing on every selection past the 5th.
# --------------------------------------------------------------------------- #
def evaluate_selection_progressive_trigger(client_record, selected_ids):
    """Per-variable selection-bias fire decision for the running selection.

    Sibling of evaluate_trigger (the realtime dwell gate) in shape -- a readiness
    gate first, then the scoped scoring -- and, like it, scored only on the CURRENTLY
    ACTIVE variables (see SCOPE below), but on the SELECTION rather than dwell.

    Returns a dict. Below readiness / no active variables:
      {"ready": False, "reason": "not_ready (...)" | "no_active_vars",
       "n_selected", "percentile_by_var": None}
    At/above readiness with an active variable set:
      {"ready":             True,
       "reason":            "ok",
       "fired":             bool,
       "target_var":        variable | None,   # the winning variable, only on a fire
       "target_percentile": float | None,      # its percentile, only on a fire
       "n_selected":        int,                # unique selected ids
       "percentile_by_var": {variable: percentile}}   # full dict, for logging

    SCOPE (Shiyao's rule, mirroring the dwell trigger): only the CURRENTLY ACTIVE
    variables are scored -- the x/y axis attributes plus any attribute with an active
    filter (get_current_axes | get_current_filters, the same active set the dwell
    trigger scopes on). The pre-scoping behavior scored every belief variable; now a
    variable the participant is not looking at or filtering on can neither earn nor
    block a fire. An empty active set is treated like dwell's no_visible_axes guard:
    not-ready, no scoring attempted.

    Reduction (Shiyao's PRIORITY HIERARCHY): THRESHOLD FIRST, THEN RANK. Build the
    candidate set from every variable whose percentile is not None AND is at/above
    SELECTION_PERCENTILE_THRESHOLD -- do NOT take a global max and threshold only the
    winner, or a high-percentile filter variable could shadow a lower-percentile axis
    variable that also cleared. Among the candidates, pick the winner by, in order:
      1. tier -- AXIS variables (on x/y) beat FILTER-only variables. A variable that
         is both on an axis and filtered counts as axis (axis membership wins).
      2. confidence -- higher elicited confidence (1-100, per variable) wins within a
         tier. Missing confidence (legacy data) sorts last within its tier, but the
         variable is still a candidate.
      3. percentile -- higher SelectionBias_v percentile wins when tier and confidence
         tie.
      4. variable name -- ascending, a deterministic final fallback so a full tie
         (same tier, confidence, and percentile) always resolves the same way.
    fired = the candidate set is non-empty; target_var / target_percentile name the
    winner (or None/None when nothing cleared). percentile_by_var is always returned
    in full so a log can show how close it got. Variables whose percentile is None
    (nothing selected present in the map -- uniform across variables) never enter the
    candidate set. A total miss (active vars present but none are belief variables)
    yields an empty percentile_by_var via the intersection in
    selection_percentile_by_var, which reduces to fired=False -- no separate path.

    Readiness: n_selected >= MIN_SELECTIONS, which is what makes this "start at
    the 5th selection."

    client_record: the CLIENTS[pid] dict (reads dc_map_detailed and beliefs, plus
                   bias_logs / response_list via get_current_axes / get_current_filters).
    selected_ids:  the participant's currently-selected teen ids.
    """
    n_selected = len(set(selected_ids))
    if n_selected < MIN_SELECTIONS:
        return {"ready": False,
                "reason": f"not_ready ({n_selected} < {MIN_SELECTIONS} selections)",
                "n_selected": n_selected,
                "percentile_by_var": None}

    # Resolve the currently-active variables (axes + active filters), the same set the
    # dwell trigger scopes on. Kept as TWO sets, not just their union, so each candidate
    # can be classified into a tier below (axis membership takes priority). Empty union
    # -> nothing to score, so guard exactly like dwell's no_visible_axes and do not
    # attempt the (expensive) null-sampling.
    axes = llm_intervention.get_current_axes(client_record)
    axis_vars = {v for v in (axes.get("x"), axes.get("y")) if v is not None}
    filter_vars = llm_intervention.get_current_filters(client_record)
    active_vars = axis_vars | filter_vars
    if not active_vars:
        return {"ready": False,
                "reason": "no_active_vars",
                "n_selected": n_selected,
                "percentile_by_var": None}

    # One fresh seeded generator for this whole evaluation, threaded through
    # selection_percentile_by_var's per-variable loop (it advances this single
    # generator, so each variable draws an independent, non-repeating null while the
    # check stays reproducible). Built ONCE here -- never per variable -- so the
    # variables do not replay an identical null distribution.
    percentile_by_var = dc_adapter.selection_percentile_by_var(
        client_record["dc_map_detailed"], selected_ids, variables=active_vars,
        rng=dc_adapter.live_rng())

    # --- Shiyao's PRIORITY HIERARCHY: THRESHOLD FIRST, then rank -----------------
    # Candidate set = every scored variable that CLEARS the threshold (percentile not
    # None and >= SELECTION_PERCENTILE_THRESHOLD). Built before any tier/confidence/
    # percentile comparison so a lower-percentile axis variable that clears can still
    # beat a higher-percentile filter variable that also clears -- the whole point.
    candidates = [v for v, p in percentile_by_var.items()
                  if p is not None and p >= SELECTION_PERCENTILE_THRESHOLD]

    def _priority(v):
        # Sort ASCENDING: axis tier (0) before filter tier (1); then negate the
        # descending keys (confidence, percentile) so higher wins; variable name last,
        # ascending, as the deterministic final tiebreak.
        tier = 0 if v in axis_vars else 1        # axis membership wins over filter-only
        confidence = _confidence_for_var(client_record, v)
        return (tier, -confidence, -percentile_by_var[v], v)

    winner = min(candidates, key=_priority) if candidates else None
    fired = winner is not None

    return {"ready": True,
            "reason": "ok",
            "fired": fired,
            # Name a target only on a fire; percentile_by_var carries the rest.
            "target_var": winner,
            "target_percentile": percentile_by_var[winner] if fired else None,
            "n_selected": n_selected,
            "percentile_by_var": percentile_by_var}
