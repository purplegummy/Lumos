"""LLM intervention for the confirmation-bias study's "LLM" condition.

Ported from the reviewed proof of concept (bias_signal_assembler.py +
confirmation_bias_llm_generator.py). The SYSTEM_PROMPT, OUTPUT_SCHEMA, tone
constraints, theme construction and input shape are the experimental stimulus
and are reproduced verbatim -- do not reword them.

Two halves:
  1. Assembly -- turn the pre-computed signals (dwell_bias_v, attended
     direction, diagnosis focus) into the structured "LLM input" the prompt
     expects. resolve_filter_range grounds each theme's filter in the real
     observed range of the dataset.
  2. Generation -- call Claude (claude-sonnet-5, low effort) with a
     schema-enforced JSON response.

Follows firebase_logger's "no-op when unconfigured" pattern: if
ANTHROPIC_API_KEY is absent the client init prints one message and disables the
feature. A missing key (or any failure) never raises and never affects the
CONTROL / AWARENESS / ADMIN conditions.
"""
import asyncio
import json
import os

from dotenv import load_dotenv

import dc_metric
import firebase_logger
import llm_trigger

# Load ANTHROPIC_API_KEY (and anything else) from server/.env if present. Safe
# no-op when the file is absent; never overrides an already-set environment.
load_dotenv()


# --------------------------------------------------------------------------- #
# 1. Assembly -- variables, ranges, phrasing (from the real dataset)
# --------------------------------------------------------------------------- #
TASK_CONTEXT = (
    "The participant is exploring a teen mental health dataset and choosing "
    "cases for a data journalism story."
)

READABLE_NAMES = {
    "screen_time_weekday": "screen time",
    "hours_sleep_weeknight": "sleep hours",
    "days_physical_activity_week": "physical activity",
    "difficulty_making_friends": "in-person social time",
}
OBSERVED_RANGES = {                       # numeric variables, real observed [min, max]
    "screen_time_weekday": (0, 8),
    "hours_sleep_weeknight": (5, 11),
    "days_physical_activity_week": (0, 7),
}
CATEGORY_LEVELS = {                       # the one categorical belief variable
    "difficulty_making_friends": ["No difficulty", "A little difficulty", "A lot of difficulty"],
}
# The study elicits SIX belief variables (dc_adapter.EXPECTED_VARIABLE_COUNT), so
# dwell_bias_v carries all six -- including child_age_years and child_sex, which
# have no grounded filter range here. Ranking must ignore them: an intervention is
# only ever about a variable we can hand the participant a real filter for.
SUPPORTED_VARIABLES = set(OBSERVED_RANGES) | set(CATEGORY_LEVELS)
RANGE_PHRASES = {                         # (variable, direction) -> natural-language phrase
    ("screen_time_weekday", "higher"): "longer screen time",
    ("screen_time_weekday", "lower"):  "shorter screen time",
    ("hours_sleep_weeknight", "higher"): "longer sleep",
    ("hours_sleep_weeknight", "lower"):  "shorter sleep",
    ("days_physical_activity_week", "higher"): "more physical activity",
    ("days_physical_activity_week", "lower"):  "less physical activity",
    ("difficulty_making_friends", "more_difficulty"): "limited in-person social time",
    ("difficulty_making_friends", "less_difficulty"): "more in-person social time",
}
DIRECTION_TO_LEVEL = {"higher": "high", "lower": "low",
                      "more_difficulty": "high", "less_difficulty": "low"}
OPPOSITE_LEVEL = {"high": "low", "low": "high"}
OPPOSITE_DIRECTION = {"higher": "lower", "lower": "higher",
                      "more_difficulty": "less_difficulty", "less_difficulty": "more_difficulty"}

THEME_SAME_PATTERN_DIFF_OUTCOME = "same pattern, different outcome"
THEME_DIFF_PATTERN_SAME_OUTCOME = "same outcome, different pattern"

SUMMARY_EVENT = "llm_summary"

DEFAULT_TONE_CONSTRAINTS = [
    "Do not mention confirmation bias",
    "Do not mention prior beliefs",
    "Do not say the participant is wrong",
    "Use concrete variable names",
    "Use an inviting and non-judgmental tone",
    "The summary should be grounded in the interaction patterns",
    "Never use an em dash; use a period or comma instead",
]


def _diagnosis_phrase(value):
    """'Yes' -> 'a depression/anxiety diagnosis', 'No' -> 'no depression/anxiety diagnosis'."""
    return "a depression/anxiety diagnosis" if value == "Yes" else "no depression/anxiety diagnosis"


def resolve_filter_range(variable, level):
    """'high'/'low' -> a concrete filter value: [lo, hi] for numeric variables,
    or a list of category labels for the one categorical variable in scope.

    This is deliberately a Python function, NOT something the LLM invents -- it has to be
    grounded in the real observed range of the dataset.
    """
    if variable in OBSERVED_RANGES:
        lo, hi = OBSERVED_RANGES[variable]
        third = (hi - lo) / 3.0
        if level == "high":
            return [round(lo + 2 * third), hi]
        return [lo, round(lo + third)]
    if variable in CATEGORY_LEVELS:
        levels = CATEGORY_LEVELS[variable]
        return levels[1:] if level == "high" else levels[:1]
    raise ValueError(f"Unknown variable for filter range resolution: {variable}")


def _variable_for_filter_ranges(candidates, filter_ranges):
    """Variable whose candidate theme has exactly these filter_ranges, or None.

    Matches by VALUE, not position. The two candidate themes are always the high
    vs. low end of the SAME variable, so their filter_ranges are distinct ([5, 8]
    vs [0, 3]; ["A little difficulty", "A lot of difficulty"] vs ["No difficulty"])
    and the model copies them through verbatim -- a reliable key back to the
    candidate that produced them. Value matching means a reordered (or
    filter_ranges-altered) model response can never mis-apply a range to a
    participant's click; a theme that matches nothing returns None and the caller
    drops its filter action.
    """
    for candidate in candidates:
        if candidate.get("filter_ranges") == filter_ranges:
            predicate = candidate.get("predicate", {})
            return next((k for k in predicate if k != "diagnosis"), None)
    return None


def _diagnosis_filter_for_filter_ranges(candidates, filter_ranges):
    """The diagnosis half of the matching candidate, as a ready-to-apply
    {variable, filter_ranges} pair, or None.

    Every candidate theme is a CONJUNCTION -- a range on the top variable AND a
    diagnosis status -- but the reviewed OUTPUT_SCHEMA carries only filter_ranges.
    Emitting the range alone leaves the participant looking at both diagnosis
    groups, including the one they were already over-attending to, which is the
    opposite of the contrast the theme names.
    """
    for candidate in candidates:
        if candidate.get("filter_ranges") == filter_ranges:
            status = candidate.get("predicate", {}).get("diagnosis")
            if status is None:
                return None
            # predicate stores the label column's own value lowercased.
            return {"variable": dc_metric.LABEL_ATTR, "filter_ranges": [status.capitalize()]}
    return None


def top_variable_contributors(dwell_bias_v, attended_direction, n=3):
    """Top-n variables by |DwellBias_v|, each paired with its attention direction,
    in the {"variable", "range"} shape the Awareness input uses."""
    supported = [kv for kv in dwell_bias_v.items() if kv[0] in SUPPORTED_VARIABLES]
    ranked = sorted(supported, key=lambda kv: abs(kv[1]), reverse=True)[:n]
    contributors = []
    for var, _score in ranked:
        direction = attended_direction.get(var)
        phrase = RANGE_PHRASES.get((var, direction), direction)
        contributors.append({"variable": READABLE_NAMES.get(var, var), "range": phrase})
    return ranked, contributors


def build_awareness_input(session):
    """The exact 'Structured Input for the LLM - Awareness' shape from the slides."""
    ranked, contributors = top_variable_contributors(
        session["dwell_bias_v"], session["attended_direction"]
    )
    diagnosis_focus = session["diagnosis_focus"]  # "Yes" or "No"
    diagnosis_status = ("diagnosed with depression/anxiety" if diagnosis_focus == "Yes"
                        else "not diagnosed with depression/anxiety")
    awareness_input = {
        "task_context": TASK_CONTEXT,
        "current_focus": {
            "diagnosis_status": diagnosis_status,
            "main_characteristics": [c["range"] for c in contributors],
        },
        "evidence_for_focus": {
            # Grounds the awareness_summary, so it must name the signal that
            # really fired. Defaulted only for realtime callers and the fixtures
            # that predate the key.
            "trigger_signal": session.get(
                "trigger_signal",
                "point-level dwell bias exceeded participant-specific threshold"),
            "top_variable_contributors": contributors,
        },
        "tone_constraints": session.get("tone_constraints", DEFAULT_TONE_CONSTRAINTS),
    }
    return awareness_input, ranked


def build_candidate_themes(session, ranked_variables):
    """The 'Mitigation' candidate_themes, built from the SINGLE top-ranked variable,
    exactly as illustrated in the slides."""
    top_var, _score = ranked_variables[0]
    direction = session["attended_direction"][top_var]
    level = DIRECTION_TO_LEVEL[direction]
    opposite_level = OPPOSITE_LEVEL[level]
    diagnosis_focus = session["diagnosis_focus"]
    opposite_diagnosis = "No" if diagnosis_focus == "Yes" else "Yes"
    same_pattern_phrase = RANGE_PHRASES.get((top_var, direction), direction)
    opposite_direction = OPPOSITE_DIRECTION[direction]
    diff_pattern_phrase = RANGE_PHRASES.get((top_var, opposite_direction), opposite_direction)
    return [
        {
            "theme_type": THEME_SAME_PATTERN_DIFF_OUTCOME,   # "same pattern, different outcome"
            "raw_theme": f"{same_pattern_phrase}, but {_diagnosis_phrase(opposite_diagnosis)}",
            "predicate": {top_var: level, "diagnosis": opposite_diagnosis.lower()},
            "filter_ranges": resolve_filter_range(top_var, level),
        },
        {
            "theme_type": THEME_DIFF_PATTERN_SAME_OUTCOME,   # "same outcome, different pattern"
            "raw_theme": f"teens with {diff_pattern_phrase} who still have {_diagnosis_phrase(diagnosis_focus)}",
            "predicate": {top_var: opposite_level, "diagnosis": diagnosis_focus.lower()},
            "filter_ranges": resolve_filter_range(top_var, opposite_level),
        },
    ]


def assemble_llm_input(session):
    awareness_input, ranked = build_awareness_input(session)
    return {
        "phase": session.get("phase", "realtime"),
        **awareness_input,
        "candidate_themes": build_candidate_themes(session, ranked),
        "previous_interventions": session.get("previous_interventions", []),
    }


# --------------------------------------------------------------------------- #
# 2. Generation -- Claude call (schema-enforced JSON)
# --------------------------------------------------------------------------- #
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_EFFORT = "low"      # latency dial; this fires while the participant is mid-task
MAX_TOKENS = 500

# The SERVER owns the deadline, not the browser. The summary blocks a
# participant's submit button behind a client-side timeout, and if the browser
# gave up first we would emit (and record) an intervention nobody saw. Keep this
# comfortably under LLM_SUMMARY_TIMEOUT_MS in main-activity/component.ts.
#
# Sized from measurement, not taste: over 6 runs latency was min 5.5s, median
# 6.0s, max 14.1s. A tighter bound drops a real share of interventions.
GENERATION_TIMEOUT_SECONDS = 20.0
# Per-attempt ceiling, deliberately GENEROUS. A tight value here is worse than
# none: the SDK retries its own timeouts, so an 8s setting turned a single slow
# call into 14.1s of retries. The wait_for above is the real deadline; this only
# stops a hung socket pinning an executor thread, which wait_for cannot cancel.
API_TIMEOUT_SECONDS = 20.0

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "awareness_summary": {"type": "string"},
        "transition": {"type": "string"},
        "recommended_themes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "filter_ranges": {"type": "array", "items": {}},
                },
                "required": ["title", "description", "filter_ranges"],
                "additionalProperties": False,   # required by the API; omitting it 400s
            },
        },
    },
    "required": ["awareness_summary", "transition", "recommended_themes"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You generate the "LLM condition" intervention for a study on confirmation bias in \
visual data exploration. A participant is exploring a teen mental health dataset to pick cases for a \
data journalism story. The system has already detected, upstream, that their exploration is trending \
toward one pattern, and has already worked out which variables and which contrastive themes are worth \
surfacing -- you are not deciding any of that, only writing the natural-language output.

You will receive a JSON payload with:
- "current_focus": the diagnosis direction and 1-3 characteristics the participant has been focused on.
- "evidence_for_focus": why this was triggered, grounding your summary so it is not invented.
- "candidate_themes": 1-2 pre-computed contrastive themes, each with a "raw_theme" description, a
  "predicate", and already-resolved "filter_ranges" (numeric range or category list) -- pass these
  filter_ranges through unchanged, do not recompute or alter them.
- "tone_constraints": a list of rules for this specific request. Follow every one of them exactly.
- "previous_interventions" (optional): earlier summaries/themes already shown this session. Vary your
  wording and theme framing from these; do not repeat the same phrasing.
- "phase": "realtime" (a live nudge) or "final_check" (a pre-submission reflection).

Produce exactly this JSON shape:
{
  "awareness_summary": "<1-2 sentences naming the pattern, grounded in current_focus / evidence_for_focus>",
  "transition": "<a short bridging sentence into the recommendation>",
  "recommended_themes": [
    {"title": "<short, concrete theme title>", "description": "<1-2 inviting sentences using the theme's raw_theme>", "filter_ranges": <copied unchanged from the matching candidate_theme>}
  ]
}

If phase is "final_check", let the transition frame this as worth a quick look before submitting rather \
than a live nudge (e.g. "Before you submit, ..."). Produce one recommended_theme per candidate_theme \
given, in the same order, each one's filter_ranges copied through exactly as given.
"""


def build_user_prompt(llm_input):
    return (
        "Here is the assembled input for this intervention:\n\n"
        f"{json.dumps(llm_input, indent=2)}\n\n"
        "Return the structured output now."
    )


# Lazy singleton client, mirroring firebase_logger._get_db: initialise on first
# use, disable the feature (print once, return None) if the key is missing or
# the SDK fails to construct. Never raises to the caller.
_client = None
_client_initialized = False


def _get_client():
    global _client, _client_initialized
    if _client_initialized:
        return _client
    _client_initialized = True

    try:
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("[llm_intervention] No ANTHROPIC_API_KEY found - LLM intervention disabled.")
            return None

        _client = anthropic.Anthropic(api_key=api_key, timeout=API_TIMEOUT_SECONDS)
        print("[llm_intervention] Anthropic client initialised.")
        return _client

    except Exception as e:
        print(f"[llm_intervention] Init failed, LLM intervention disabled: {e}")
        return None


def generate_with_usage(llm_input, model=DEFAULT_MODEL, effort=DEFAULT_EFFORT):
    """Generate one intervention, returning (result, usage).

    result: the parsed dict, or None on any failure (missing key, API error,
    unparseable response). usage: {"input_tokens", "output_tokens"} or None.
    Never raises.

    This is the single call path. Both the live server log and the offline CLI
    read their token counts from here, so the numbers always come from the same
    place. generate() wraps this for callers that only want the result.
    """
    client = _get_client()
    if client is None:
        return None, None

    try:
        import anthropic
    except Exception:
        return None, None

    user_prompt = build_user_prompt(llm_input)

    def _call(include_effort):
        output_config = {"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}}
        if include_effort:
            output_config["effort"] = effort
        return client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            output_config=output_config,
            messages=[{"role": "user", "content": user_prompt}],
        )

    try:
        try:
            message = _call(True)
        except anthropic.BadRequestError as e:
            # Some models reject the effort parameter; retry once without it,
            # re-raise anything else. (claude-sonnet-5 supports effort, so this
            # is a safety net, not the expected path.)
            if "effort" in str(e).lower():
                print("[llm_intervention] 'effort' rejected; retrying without it.", flush=True)
                message = _call(False)
            else:
                raise
        # output_config.format guarantees the first text block is valid JSON.
        text = next((b.text for b in message.content if b.type == "text"), None)
        if not text:
            return None, None
        raw_usage = getattr(message, "usage", None)
        usage = None
        if raw_usage is not None:
            usage = {
                "input_tokens": getattr(raw_usage, "input_tokens", None),
                "output_tokens": getattr(raw_usage, "output_tokens", None),
            }
        return json.loads(text), usage
    except Exception as e:
        print(f"[llm_intervention] generation failed: {e}", flush=True)
        return None, None


def generate(llm_input, model=DEFAULT_MODEL, effort=DEFAULT_EFFORT):
    """Generate one intervention. Returns the parsed dict, or None on any failure
    (missing key, API error, unparseable response). Never raises.

    Thin wrapper over generate_with_usage for callers that don't need tokens.
    """
    result, _usage = generate_with_usage(llm_input, model=model, effort=effort)
    return result


def selection_metrics(detailed_map, selected_ids):
    """Selection-weighted counterpart of dc_adapter.compute_dwell_metrics.

    dwell_bias / dwell_bias_v take a generic {teen_id: weight} map, so weighting
    every selected teen equally gives the selection-side metrics with no change to
    dc_metric: the scalar reduces to dc_metric.selection_bias (mean DC over the
    selection minus the all-teen mean, the number already logged as
    dc_bias.selection_bias on qualifying interactions once priors are committed),
    and the per-variable version is the breakdown that compute_phase_metrics does
    not expose but the prompt assembly needs.

    Lives here rather than in dc_adapter because that module is out of scope for
    this feature.

    FAIL-LOUD, inherited: a selected id absent from detailed_map raises KeyError.
    The caller guards at the handler boundary, as on_interaction does for dwell.
    """
    weights = {teen_id: 1.0 for teen_id in selected_ids}
    return {
        "selection_bias": dc_metric.dwell_bias(detailed_map, weights),
        "selection_bias_v": dc_metric.dwell_bias_v(detailed_map, weights),
        "n_selected": len(weights),
    }


def live_room(sio, sid_by_pid, pid):
    """The participant's CURRENTLY CONNECTED room, or None.

    Two hazards this closes, both invisible without it:

    1. sid_by_pid is never pruned (server.disconnect only timestamps), so after a
       reconnect it holds the OLD sid. socket.io discards an emit to a dead room
       silently -- no error, no return value -- so the caller would report success
       for a message nobody received.
    2. room=None is NOT "nobody" in socket.io: base_manager.connect enters every
       client into room None, so it is the ALL-CLIENTS room. Emitting there would
       broadcast one participant's personalised intervention to every other
       participant in the study.
    """
    sid = sid_by_pid.get(pid)
    if sid is None or not sio.manager.is_connected(sid, "/"):
        return None
    return sid


async def emit_summary_skip(sio, room, reason, pid=None):
    """Tell the frontend no summary is coming, so it can proceed to submit.

    The participant's submit button waits on a SUMMARY_EVENT, so every path that
    does not produce one -- gate not met, no dc_map, dead API key -- has to say so
    explicitly. Staying silent would leave them unable to finish the study. The
    frontend tells the two apart by the absence of recommended_themes.

    Persists the outcome too: without it the treated-vs-untreated split for the
    summary conditions exists only in stdout, and a participant who was gated out
    is indistinguishable in the data from one who was never in the condition.
    """
    if room is not None:
        await sio.emit(SUMMARY_EVENT, {"reason": reason}, room=room)
    if pid:
        firebase_logger.save_logs(pid, [{
            "kind": "llm_summary_skipped",
            "participant_id": pid,
            "created_at": _now(),
            "reason": reason,
            "delivered": room is not None,
        }])


async def generate_and_emit(sio, sid_by_pid, pid, client_record, dwell_metrics, teens):
    """Background task: assemble -> generate -> emit the realtime intervention.

    Fired via SIO.start_background_task from on_interaction AFTER the interaction
    response has already gone out, so the ~4-6s Claude call is off the critical
    path.

    Takes the pid -> sid map rather than a sid: a reconnect during those seconds
    gives the participant a NEW sid, and emitting to the old room silently
    delivers nothing. live_room resolves it and rejects a stale one.

    Emits an "llm_intervention" event carrying the generated object with each
    theme's variable / diagnosis_filter attached (see _generate_and_emit) -- the
    shape the frontend panel already consumes.
    """
    await _generate_and_emit(
        sio, sid_by_pid, pid, client_record, teens,
        weights=dc_metric.dwell_by_teen(client_record.get("bias_logs", [])),
        bias_v=dwell_metrics.get("dwell_bias_v", {}),
        phase="realtime",
        trigger_signal="point-level dwell bias exceeded participant-specific threshold",
        event="llm_intervention")


async def generate_summary_and_emit(sio, sid_by_pid, pid, client_record,
                                    selection, teens, selected_ids):
    """Background task: the same pipeline, run on the FINAL SELECTION at submit.

    Differs from generate_and_emit only in the arguments below: the teens are
    weighted equally by having been selected rather than by dwell time, the
    per-variable scores come from the selection, the phase is "final_check"
    (which the prompt already frames as "before you submit"), the trigger signal
    names the selection, and the event is SUMMARY_EVENT.

    Emits exactly one SUMMARY_EVENT either way -- a generation that failed, timed
    out, or could not be delivered still has to release the submit button.
    """
    generated = await _generate_and_emit(
        sio, sid_by_pid, pid, client_record, teens,
        weights={teen_id: 1.0 for teen_id in selected_ids},
        bias_v=selection.get("selection_bias_v", {}),
        phase="final_check",
        trigger_signal="selection-level bias over the participant's final selection "
                       "exceeded participant-specific threshold",
        event=SUMMARY_EVENT)
    if not generated:
        await emit_summary_skip(sio, live_room(sio, sid_by_pid, pid),
                                "generation_failed", pid)


async def _generate_and_emit(sio, sid_by_pid, pid, client_record, teens,
                             weights, bias_v, phase, trigger_signal, event):
    """Shared assemble -> generate -> emit -> persist core. Returns whether it DELIVERED.

    False covers "generation failed", "timed out", and "generated but the
    participant had no live socket" -- the caller has to treat all three the same
    way, because in all three the participant never saw anything.

    Everything downstream of the weights is phase-agnostic: derive_attended_direction
    and _majority_diagnosis both take a {teen_id: weight} map, so the realtime and
    summary paths differ only in the arguments above.

    Wrapped end-to-end in try/except: a broken LLM call must never break an
    interaction response or lose data. Appends a compact record to
    client_record["llm_interventions"], which is fed back as previous_interventions
    so later generations (including a summary following realtime nudges) vary their
    wording, and persists the full input/output via firebase_logger.
    """
    delivered = False   # set before the try: the except clauses read it
    try:
        variables = list(bias_v.keys())

        attended_direction = llm_trigger.derive_attended_direction(teens, weights, variables)
        diagnosis_focus = _majority_diagnosis(teens, weights)

        session = {
            "phase": phase,
            # Carries selection-weighted scores on the final_check path. The key
            # keeps its dwell name so the llm_samples fixtures stay valid.
            "dwell_bias_v": bias_v,
            "attended_direction": attended_direction,
            "diagnosis_focus": diagnosis_focus,
            "trigger_signal": trigger_signal,
            "previous_interventions": client_record.get("llm_interventions", []),
        }
        llm_input = assemble_llm_input(session)

        # --- log what actually goes into the model -------------------------
        _top = [c.get("variable") for c in
                llm_input.get("evidence_for_focus", {}).get("top_variable_contributors", [])]
        _themes = " | ".join(
            f"{t.get('raw_theme')} -> {t.get('filter_ranges')}"
            for t in llm_input.get("candidate_themes", []))
        print(f"[LLM] {pid}: input focus={diagnosis_focus} top={_top} "
              f"phase={llm_input.get('phase')} "
              f"prev={len(llm_input.get('previous_interventions', []))}", flush=True)
        print(f"[LLM] {pid}: themes {_themes}", flush=True)

        started = _now()
        # run_in_executor, not a direct call: the Anthropic SDK client is
        # SYNCHRONOUS, and start_background_task returns an asyncio.Task on this
        # same loop -- so calling it inline froze the whole server (every
        # participant's interactions, not just this one) for the length of the
        # HTTP request. wait_for then bounds the total, including the
        # BadRequestError retry, which a per-call SDK timeout would not.
        loop = asyncio.get_event_loop()
        result, usage = await asyncio.wait_for(
            loop.run_in_executor(None, generate_with_usage, llm_input),
            timeout=GENERATION_TIMEOUT_SECONDS)
        elapsed = (_now() - started) / 1000.0
        if result is None:
            # Disabled (no API key) or generation failed; both already logged a
            # line of their own. Say so here so the trigger has a visible end.
            print(f"[LLM] {pid}: no output ({elapsed:.1f}s)", flush=True)
            return False

        # Attach both halves of each recommended theme's filter: the variable its
        # range applies to, and the diagnosis status it contrasts against. The
        # reviewed OUTPUT_SCHEMA deliberately carries only title/description/
        # filter_ranges, so both would otherwise be lost. We recover them by
        # matching the theme's filter_ranges back to the candidate that produced
        # it -- by VALUE, never by position (see below). Enriches the emitted
        # event only; schema/prompt untouched.
        candidates = llm_input.get("candidate_themes", [])
        for theme in result.get("recommended_themes", []):
            variable = _variable_for_filter_ranges(candidates, theme.get("filter_ranges"))
            if variable is None:
                # No exact-value match: the model reordered or (against the
                # prompt) altered the filter_ranges. Drop this theme's filter
                # action rather than guess -- applying a wrong range to a real
                # click would silently corrupt the stimulus. The frontend
                # already no-ops a theme with no "variable".
                print(f"[LLM] {pid}: theme filter_ranges "
                      f"{theme.get('filter_ranges')} matched no candidate; "
                      f"dropping its filter action.", flush=True)
                continue
            theme["variable"] = variable
            theme["diagnosis_filter"] = _diagnosis_filter_for_filter_ranges(
                candidates, theme.get("filter_ranges"))

        # Feed back next time so repeat interventions vary their wording.
        client_record.setdefault("llm_interventions", []).append({
            "awareness_summary": result.get("awareness_summary"),
            "recommended_theme_titles": [t.get("title") for t in result.get("recommended_themes", [])],
        })

        # --- log what the model produced -----------------------------------
        _titles = [t.get("title") for t in result.get("recommended_themes", [])]
        _tokens = (f"in={usage.get('input_tokens')} out={usage.get('output_tokens')}"
                   if usage else "n/a")
        print(f"[LLM] {pid}: output summary={result.get('awareness_summary')!r}", flush=True)
        print(f"[LLM] {pid}: output transition={result.get('transition')!r}", flush=True)
        print(f"[LLM] {pid}: output themes={_titles} "
              f"({elapsed:.1f}s, tokens {_tokens})", flush=True)

        room = live_room(sio, sid_by_pid, pid)
        if room is not None:
            await sio.emit(event, result, room=room)
            delivered = True
        else:
            print(f"[LLM] {pid}: {event} not delivered (no live socket)", flush=True)

        # Persist the generated text so it is recoverable for analysis. This is
        # research data, not just UI. Reuse the existing per-participant logs path.
        firebase_logger.save_logs(pid, [{
            # The event name doubles as the record kind, so realtime nudges and
            # pre-submission summaries stay separable in the analysis data.
            "kind": event,
            "participant_id": pid,
            "created_at": _now(),
            # Generated is not seen. Without this an undelivered intervention
            # would count as treatment in the analysis.
            "delivered": delivered,
            "llm_input": llm_input,
            "output": result,
        }])
        return delivered
    except asyncio.TimeoutError:
        print(f"[LLM] {pid}: {event} timed out after "
              f"{GENERATION_TIMEOUT_SECONDS}s", flush=True)
        return False
    except Exception as e:
        # Return what actually reached the participant, not False. A failure
        # AFTER the emit (persistence, logging) must not make the caller send a
        # second "nothing to show" for a summary they are already looking at.
        print(f"[LLM] {event} failed: {e}", flush=True)
        return delivered


def _majority_diagnosis(teens, weights):
    """WEIGHTED majority diagnosis status among the teens carrying weight.

    Weighted by whatever drove this generation, consistent with the rest of the
    pipeline (dwell_bias, dwell_bias_v, derive_attended_direction).

    On the REALTIME path the weights are dwell times, and that matters: a plain
    head count would let diagnosis_focus contradict the dwell signal that fired
    the trigger (e.g. long dwells on a few diagnosed teens plus brief glances at
    many non-diagnosed ones), which would invert current_focus and flip both
    candidate_themes -- recommending the participant's own fixation back to them.

    On the SUMMARY path every weight is 1.0, so this does reduce to a head count
    over the selection. That is intended, not an oversight: a teen is either in
    the final selection or not, so each one counts once.

    Returns "Yes" when diagnosed teens hold MORE than half the total weight, else
    "No" (ties -> "No", matching the dataset's own majority). Falls back to "No"
    when no weighted teen carries the label column.
    """
    diagnosed_weight = 0.0
    total_weight = 0.0
    for teen_id, weight in weights.items():
        teen = teens.get(teen_id)
        if teen is None or dc_metric.LABEL_ATTR not in teen:
            continue
        total_weight += weight
        if str(teen[dc_metric.LABEL_ATTR]) == dc_metric.DIAGNOSED_VALUE:
            diagnosed_weight += weight
    if total_weight == 0:
        return "No"
    return "Yes" if diagnosed_weight > total_weight / 2 else "No"


def _now():
    """Millisecond timestamp, matching the rest of the server's logging."""
    import bias_util
    return bias_util.get_current_time()
