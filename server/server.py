"""Server for interfacing with the frontend.
"""
import os
from pathlib import Path

import pandas as pd
import socketio
from aiohttp import web
from aiohttp_index import IndexMiddleware

import bias
import bias_util
import firebase_logger
import dc_metric
import dc_adapter
import llm_intervention
import llm_trigger

# Set the path for the Google Cloud Logging logger
currdir = Path(__file__).parent.absolute()

# Debug toggle: when True, on_interaction streams the per-attribute JS
# confirmation-bias scores to stdout. Off by default; flip on when building/
# debugging the next layer.
JS_DEBUG = False

CLIENTS = {}  # entire data map of all client data
# pid -> last logged "not triggered" gate code. Keeps the LLM skip log to one
# line per gate change instead of one per interaction.
LLM_LAST_SKIP = {}
CLIENT_PARTICIPANT_ID_SOCKET_ID_MAPPING = {}
CLIENT_SOCKET_ID_PARTICIPANT_MAPPING = {}
COMPUTE_BIAS_FOR_TYPES = [
    "mouseout_item",
    "mouseout_group",
    "click_group",
    "click_add_item",
    "click_remove_item",
]

SIO = socketio.AsyncServer(cors_allowed_origins='*')
APP = web.Application(middlewares=[IndexMiddleware()])
SIO.attach(APP)

async def handle_ui_files(request):
    # Extract the requested file name
    fname = request.match_info.get('fname', 'index.html')

    # Serve index.html for all routes that don't have a file extension
    if '.' not in fname:
        fname = 'index.html'

    # Define the public directory (similar to Flask's 'public' directory)
    public_dir = os.path.join(os.path.dirname(__file__), 'public')

    # Serve the file from the public directory
    file_path = os.path.join(public_dir, fname)

    try:
        return web.FileResponse(file_path)
    except FileNotFoundError:
        raise web.HTTPNotFound()

# Static file serving
APP.router.add_static('/static/', path=str(os.path.join(os.path.dirname(__file__), 'public')), name='static')

# Dynamic routing for all paths, similar to Flask's catch-all routes
APP.router.add_route('GET', '/{fname:.*}', handle_ui_files)

@SIO.event
async def connect(sid, environ):
    print(f"Connected: {sid}")
    attr_dist = {}
    for filename in bias.DATA_MAP:
        dataset = bias.DATA_MAP[filename]
        attr_dist[filename] = dataset["distribution"]
    await SIO.emit("attribute_distribution", attr_dist, room=sid)


@SIO.event
def disconnect(sid):
    if sid in CLIENT_SOCKET_ID_PARTICIPANT_MAPPING:
        pid = CLIENT_SOCKET_ID_PARTICIPANT_MAPPING[sid]
        if pid in CLIENTS:
            CLIENTS[pid]["disconnected_at"] = bias_util.get_current_time()
            print(f"Disconnected: Participant ID: {pid} | Socket ID: {sid}")


@SIO.event
async def on_session_end_page_level_logs(sid, payload):
    pid = payload["participantId"]
    if pid in CLIENTS and "data" in payload:
        dirname = f"output/{CLIENTS[pid]['app_type']}/{pid}"
        Path(dirname).mkdir(exist_ok=True) 
        filename = f"output/{CLIENTS[pid]['app_type']}/{pid}/session_end_page_logs_{pid}_{bias_util.get_current_time()}.tsv"
        df_to_save = pd.DataFrame(payload["data"])

        # persist to disk
        df_to_save.transpose().to_csv(filename, sep="\t")

        print(f"Saved session logs to file: {filename}")


@SIO.event
async def on_save_logs(sid, data):
    if sid in CLIENT_SOCKET_ID_PARTICIPANT_MAPPING:
        pid = CLIENT_SOCKET_ID_PARTICIPANT_MAPPING[sid]
        if pid in CLIENTS:
            dirname = f"output/{CLIENTS[pid]['app_type']}/{pid}"
            Path(dirname).mkdir(exist_ok=True)
            ts = bias_util.get_current_time()

            filename = f"{dirname}/logs_{pid}_{ts}.tsv"
            df_to_save = pd.DataFrame(CLIENTS[pid]["response_list"])
            df_to_save.to_csv(filename, sep="\t")
            print(f"Saved logs to file: {filename}")
            firebase_logger.save_logs(pid, CLIENTS[pid]["response_list"])

            priors = CLIENTS[pid].get("priors", {})
            if priors:
                priors_filename = f"{dirname}/priors_{pid}_{ts}.tsv"
                pd.DataFrame(priors.values()).to_csv(priors_filename, sep="\t", index=False)
                print(f"Saved priors to file: {priors_filename}")
            firebase_logger.save_priors(pid, priors)

def ensure_client(sid, pid, app_mode, app_type, app_level, participant_id_source="random"):
    """Get-or-create the per-participant record, applying dataset/level resets.

    Shared by on_interaction and on_commit_priors so both entry points
    initialize the same record the same way (and so priors committed *before*
    any interaction still land in a well-formed record). Returns the record.
    """
    # Let these get updated everytime an event occurs, to handle the
    #   worst case scenario of random restart of the server.
    CLIENT_SOCKET_ID_PARTICIPANT_MAPPING[sid] = pid
    CLIENT_PARTICIPANT_ID_SOCKET_ID_MAPPING[pid] = sid

    if pid not in CLIENTS:
        # new participant => establish data mapping for them!
        CLIENTS[pid] = {}
        CLIENTS[pid]["id"] = sid
        CLIENTS[pid]["participant_id"] = pid
        CLIENTS[pid]["app_mode"] = app_mode
        CLIENTS[pid]["app_type"] = app_type
        CLIENTS[pid]["app_level"] = app_level
        CLIENTS[pid]["connected_at"] = bias_util.get_current_time()
        CLIENTS[pid]["participant_id_source"] = participant_id_source
        CLIENTS[pid]["bias_logs"] = []
        CLIENTS[pid]["response_list"] = []
        CLIENTS[pid]["priors"] = {}  # {attribute: PriorBelief}, last-write-wins

    if app_mode != CLIENTS[pid]["app_mode"] or app_level != CLIENTS[pid]["app_level"]:
        # datasets have been switched => reset the logs array!
        # OR
        # app_level (e.g. practice > live) is changed but same dataset is in use => reset the logs array!
        # Priors are per-dataset, so a dataset/level switch invalidates them too.
        CLIENTS[pid]["app_mode"] = app_mode
        CLIENTS[pid]["app_level"] = app_level
        CLIENTS[pid]["bias_logs"] = []
        CLIENTS[pid]["response_list"] = []
        CLIENTS[pid]["priors"] = {}

    return CLIENTS[pid]


@SIO.on('on_commit_priors')
async def on_commit_priors(sid, data):
    """Receive elicited prior beliefs and stash them per-participant."""
    print(f"[on_commit_priors] received from sid={sid}")
    try:
        app_mode = data["appMode"]
        app_type = data.get("appType")
        app_level = data.get("appLevel")
        pid = data["participantId"]
        pid_source = data.get("participantIdSource", "random")

        client = ensure_client(sid, pid, app_mode, app_type, app_level, pid_source)

        incoming = data.get("priors", [])
        if isinstance(incoming, dict):
            incoming = [incoming]
        for belief in incoming:
            condition = belief.get("condition", "default")
            key = f"{belief['attribute']}::{condition}"
            print(f"[on_commit_priors] attribute={belief.get('attribute')} condition={condition} key={key}")
            client["priors"][key] = belief

        print(f"[on_commit_priors] priors keys now: {sorted(client['priors'].keys())}")
        firebase_logger.save_priors(pid, client["priors"])
        firebase_logger.save_meta(pid, client)

        # --- DC map: compute ONCE all six variables have BOTH conditions ------
        # Reshape stored priors via the adapter (guards incomplete vars + asserts
        # shared bins). When ready, compute the per-teen DC map a single time and
        # cache it on the client record for compute_metrics to read live.
        ready, beliefs, report = dc_adapter.is_ready(client["priors"])
        print(f"[DC] {pid}: {len(report['complete'])}/{dc_adapter.EXPECTED_VARIABLE_COUNT} "
              f"variables complete {report['complete']}"
              + (f" | incomplete-so-far {report['skipped_incomplete']}" if report['skipped_incomplete'] else ""),
              flush=True)
        if ready:
            teens = bias.DATA_MAP.get(app_mode, {}).get("data", {})
            sample = next(iter(teens.values()), {})
            if teens and dc_metric.LABEL_ATTR in sample:
                dmap = dc_metric.dc_map(teens, beliefs)
                client["dc_map"] = dmap
                # Detailed map (per-teen {dc, consistency, weights}) for the dwell
                # metrics; same teens/beliefs, computed once alongside dc_map.
                client["dc_map_detailed"] = dc_metric.dc_map_detailed(teens, beliefs)
                mean_dc = sum(dmap.values()) / len(dmap) if dmap else 0.0
                print(f"[DC] DC map computed for {pid}: {len(dmap)} teens, "
                      f"mean DC {mean_dc:.4f} (vars: {report['complete']})", flush=True)
            else:
                print(f"[DC] {pid}: dataset '{app_mode}' has no '{dc_metric.LABEL_ATTR}' "
                      f"label column; skipping DC map.", flush=True)
    except Exception as e:
        print(f"[on_commit_priors] ERROR: {e}", flush=True)
        raise


@SIO.on('on_selected_subjects')
async def on_selected_subjects(sid, data):
    pid = data.get("participantId")
    subjects = data.get("selected_subjects", [])
    if pid:
        firebase_logger.save_selected_subjects(pid, subjects)
        print(f"Selected subjects for {pid}: {subjects}")


@SIO.on('on_task_submitted')
async def on_task_submitted(sid, data):
    pid = data.get("participantId")
    code = data.get("verificationCode")
    subjects = data.get("selected_subjects", [])
    if pid:
        firebase_logger.save_task_submission(pid, code, subjects)
        print(f"Task submitted for {pid}: {code}")


@SIO.event
async def on_interaction(sid, data):
    app_mode = data["appMode"]  # The dataset that is being used, e.g. cars.csv
    app_type = data["appType"]  # CONTROL / AWARENESS / ADMIN
    app_level = data["appLevel"]  # live / practice
    pid = data["participantId"]
    pid_source = data.get("participantIdSource", "random")
    interaction_type = data["interactionType"] # Interaction type - eg. hover, click

    ensure_client(sid, pid, app_mode, app_type, app_level, pid_source)

    # record response to interaction
    response = {}
    response["sid"] = sid
    response["participant_id"] = pid
    response["app_mode"] = app_mode
    response["app_type"] = app_type
    response["app_level"] = app_level
    response["processed_at"] = bias_util.get_current_time()
    response["interaction_type"] = interaction_type
    response["input_data"] = data

    # check whether to compute bias metrics or not
    if interaction_type in COMPUTE_BIAS_FOR_TYPES:
        CLIENTS[pid]["bias_logs"].append(data)
        metrics = bias.compute_metrics(app_mode, CLIENTS[pid]["bias_logs"], CLIENTS[pid]["priors"])

        # --- DC phase metrics: read the CACHED dc_map (never recompute here) ---
        # Present only once elicitation is complete; absent/None otherwise (not
        # an error). Ids come from the same bias_logs the JS metric consumes.
        dc_map_cached = CLIENTS[pid].get("dc_map")
        if dc_map_cached:
            dc_bias = dc_adapter.compute_phase_metrics(dc_map_cached, CLIENTS[pid]["bias_logs"])
            metrics["dc_bias"] = dc_bias
            print(f"[DC] pid={pid} | n_logs={len(CLIENTS[pid]['bias_logs'])} | "
                  f"real_time={dc_bias['real_time_bias']:+.4f} "
                  f"overall={dc_bias['overall_interaction_bias']:+.4f} "
                  f"selection={dc_bias['selection_bias']:+.4f} "
                  f"(interacted={dc_bias['n_interacted']}, selected={dc_bias['n_selected']})",
                  flush=True)
        else:
            metrics["dc_bias"] = None

        # --- Dwell-weighted bias: read the CACHED detailed map (parallel to
        # dc_bias, a SEPARATE top-level key). dwell_bias/dwell_bias_v raise
        # fail-loud on an unknown id; on_interaction has no outer try/except, so
        # an unguarded raise would drop the whole interaction response (lost data).
        # Guard ONLY here at the handler boundary -- the strict raise stays inside
        # dc_metric. On failure: log + None, same shape as the map-absent case.
        detailed = CLIENTS[pid].get("dc_map_detailed")
        if detailed:
            try:
                metrics["dwell_bias"] = dc_adapter.compute_dwell_metrics(
                    detailed, CLIENTS[pid]["bias_logs"])
            except Exception as e:
                print(f"[DWELL] compute failed: {e}", flush=True)
                metrics["dwell_bias"] = None
        else:
            metrics["dwell_bias"] = None

        response["output_data"] = metrics

        # === JS DEBUG (toggle via JS_DEBUG flag near top of file) === start
        # Read-only stream of the JS confirmation-bias scores already in
        # output_data, one block per qualifying interaction. Off by default;
        # flip JS_DEBUG to re-enable when building the next layer.
        if JS_DEBUG:
            _js_metric = metrics["js_divergence"][0]  # {attr: score} (drop details)
            _n = len(CLIENTS[pid]["bias_logs"])
            if _js_metric:
                _scores = "  ".join(f"{a}={s}" for a, s in _js_metric.items())
            else:
                _scores = "(other attrs: no prior)"
            print(f"[JS DEBUG] pid={pid} | n={_n} | {_scores}", flush=True)
        # === JS DEBUG === end
    else:
        response["output_data"] = None

    # save response
    CLIENTS[pid]["response_list"].append(response)

    await SIO.emit("log", response)  # send this to all
    await SIO.emit("interaction_response", response, room=sid)
    firebase_logger.save_logs(pid, [response])

    # --- LLM intervention (condition: appType == "LLM") -----------------------
    # Runs AFTER the interaction response has already been emitted above, so the
    # ~4-6s Claude call is off the critical path. Fired as a background task that
    # emits a SEPARATE "llm_intervention" event on completion; never inlined into
    # output_data. Guarded: a broken LLM path must never break an interaction.
    _out = response.get("output_data")
    if app_type == "LLM" and _out and _out.get("dwell_bias"):
        try:
            client_record = CLIENTS[pid]
            teens = bias.DATA_MAP.get(app_mode, {}).get("data", {})
            _dwell = _out["dwell_bias"]
            fired, reason = llm_trigger.evaluate_trigger(client_record, _dwell)
            if fired:
                client_record["llm_last_fired_at"] = bias_util.get_current_time()
                LLM_LAST_SKIP.pop(pid, None)
                print(f"[LLM] {pid}: triggered (dwell_bias={_dwell.get('dwell_bias'):+.4f}, "
                      f"n_dwelled={_dwell.get('n_dwelled')})", flush=True)
                SIO.start_background_task(
                    llm_intervention.generate_and_emit,
                    SIO, sid, pid, client_record, _dwell, teens)
            else:
                # Log only when the blocking gate CHANGES, so exploring shows
                # why nothing fired without a line on every interaction.
                code = reason.split(" ")[0]
                if LLM_LAST_SKIP.get(pid) != code:
                    LLM_LAST_SKIP[pid] = code
                    print(f"[LLM] {pid}: not triggered ({reason})", flush=True)
        except Exception as e:
            print(f"[LLM] trigger failed: {e}", flush=True)


if __name__ == "__main__":
    bias.precompute_distributions()
    port = int(os.environ.get("PORT", 3000))
    web.run_app(APP, port=port)
