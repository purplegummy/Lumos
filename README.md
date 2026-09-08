# Confirmation Bias and LLM Interventions in Visual Data Analysis

Research software for a controlled study of **confirmation bias during exploratory visual
data analysis**, and of whether **real-time LLM-generated interventions** can surface that
bias to the analyst while it is happening.

Participants first state their prior beliefs about a dataset, then explore that dataset in
an interactive scatterplot and select a subset of records. The system continuously scores
how far their *attention* and their *choices* lean toward records that confirm their stated
priors, and — in the intervention conditions — interrupts with a short, targeted nudge when
that lean crosses a calibrated threshold.

The visualization frontend is built on Lumos, originally designed by Narechania et al.
\[1\]; the belief elicitation, bias metrics, trigger logic, and LLM intervention
pipeline in `server/` are contributed by this project.


## Study design

**Task.** Participants examine a dataset of 200 adolescent records (demographics,
screen time, sleep, physical activity, social difficulty, and a depression/anxiety diagnosis
label) and select 10 records for a hypothetical follow-up.

**Two-part flow.** The study runs as two independent visits so the elicited priors are fixed
before exploration begins:

| Route | Phase |
|---|---|
| `/elicitation` | Part 1 — state prior beliefs about each variable, split by diagnosis status |
| `/main` | Part 2 — explore the data and make the selection |

Priors are persisted per participant and reloaded at the start of Part 2, so the two phases
may run in separate sessions or separate server processes.

**Conditions** (`?type=` URL parameter):

| Condition | Behavior |
|---|---|
| `CONTROL` | No feedback. Metrics are still computed and logged. |
| `LLM` | Real-time LLM interventions triggered by dwell bias during exploration. |
| `LLM_SUMMARY` | A single LLM intervention triggered by selection bias mid-selection. |
| `LLM_BOTH` | Both of the above. |

A `?tutorial=1` walkthrough runs the same interface over a small, unrelated housing dataset
so the tutorial never reads as real study data.


## Bias metrics

Belief consistency is scored per variable against each participant's own elicited priors
(`server/dc_metric.py`, held frozen and unit-tested; `server/dc_adapter.py` is the reshape
and integration layer around it).

- **DwellBias** — how far hover attention concentrates on records consistent with the priors.
  Attention is attributed *per variable*, fixed at the moment of each hover, using the axis
  and filter state in effect at that instant.
- **SelectionBias** — the same question asked of the records actually selected, attributed to
  the variable the participant was looking at when each selection was made.

Both are reported as **percentiles against a Monte Carlo null distribution** rather than as
raw values, so a score is interpretable as "more extreme than X% of chance behavior at this
sample size." Null sampling is seeded from a single fixed constant
(`dc_adapter.LIVE_SIMULATION_SEED`), so a given check depends only on its inputs — never on
call order or session history.


## Intervention triggers

`server/llm_trigger.py` decides *whether* to interrupt and *on which variable*.

**Dwell (realtime).** A variable becomes checkable once it has accumulated
`MIN_ELIGIBLE_DWELL_SECONDS = 20` of its own eligible dwell, and is re-checked at most once
per `DWELL_RECHECK_SECONDS = 10` of new eligible dwell. It fires at
`DWELL_PERCENTILE_THRESHOLD = 0.80`.

**Selection (mid-task).** Checked once each at 5, 7, and 9 selections, requiring
`MIN_SELECTIONS = 5` and firing at `SELECTION_PERCENTILE_THRESHOLD = 0.80`.

**Reduction.** Both triggers score every currently active variable independently and then
reduce the resulting `{variable: percentile}` map through one shared priority hierarchy:
threshold first, then axis-tier > filter-tier > elicited confidence > percentile > name.

**Display-linked pause.** While an intervention panel is on screen, the dwell trigger is
paused — not for a fixed duration, but for exactly as long as the panel is actually visible.
A watchdog (`PANEL_FLAG_WATCHDOG_MS`) is a backstop against a lost dismissal, not a policy
value.

Generation calls the Anthropic API (`claude-sonnet-5`) with a constrained JSON output schema
(`server/llm_intervention.py`). `scripts/run_llm_sample.py` exercises that pipeline offline
against the fixtures in `scripts/llm_samples/`, with no server and no sockets.


## Repository layout

```
app/                 Angular 7 frontend
server/
  server.py            aiohttp + socket.io server, interaction log ingestion
  bias.py              dataset loading and distribution precomputation
  dc_metric.py         belief-consistency math (frozen, unit-tested)
  dc_adapter.py        priors -> beliefs -> DC map integration layer
  llm_trigger.py       when to fire, and on which variable
  llm_intervention.py  prompt assembly, generation, delivery
  firebase_logger.py   Firestore persistence
  data/                datasets
  public/              built frontend, served by server.py
  tests/               test suite
  scripts/             offline analysis and sample-runner scripts
```


## Setup

Per-directory instructions:

- [`app`](app) — frontend (Angular 7, Node 22)
- [`server`](server) — backend (Python 3.10)

The LLM conditions require an `ANTHROPIC_API_KEY`; copy `server/.env.example` to
`server/.env` and fill it in. `CONTROL` runs without one.

Firestore logging activates only when credentials are supplied
(`FIREBASE_SERVICE_ACCOUNT_JSON` or `GOOGLE_APPLICATION_CREDENTIALS`); without them the
server starts normally and simply does not persist remotely.


## Tests

From `server/`:

```
python -m pytest                 # collected tests
python tests/test_x.py           # each file is also a standalone harness
```

Several suites are hand-rolled `main()` + `check()` harnesses rather than pytest cases, so
running them directly is the only way to exercise every assertion.


## Deployment

The app deploys as a single service: the Angular build is emitted into `server/public/`
and served by the Python backend.

- Build the frontend from `app/`: `ng build`
  (writes to `../server/public/` per `app/angular.json` > `outputPath`)
- Confirm `app/src/app/models/config.ts` > `DeploymentConfig.SERVER_URL` points at the
  deployed backend, not `localhost`.
- Commit the rebuilt `server/public/` output.
- Push. The host builds from `server/` using `server/Procfile`
  (`web: python -m pip install pandas && python server.py`) and `server/runtime.txt` (Python 3.10).
- Set `ANTHROPIC_API_KEY` and the Firebase credential as environment variables in the
  host's dashboard — never in the repository.


## License

The software is available under the [MIT License](LICENSE).


## References

\[1\] - Narechania, Arpit and Coscia, Adam and Wall, Emily and Endert, Alex.
"Lumos: Increasing Awareness of Analytic Behavior during Visual Data Analysis."
*IEEE Transactions on Visualization and Computer Graphics* 28, no. 1 (2022): 1009-1018.

```bibTeX
@article{narechania2022lumos,
  author={Narechania, Arpit and Coscia, Adam and Wall, Emily and Endert, Alex},
  journal={{IEEE Transactions on Visualization and Computer Graphics}}, 
  title={{Lumos: Increasing Awareness of Analytic Behavior during Visual Data Analysis}}, 
  year={2022},
  volume={28},
  number={1},
  pages={1009-1018},
  doi={10.1109/TVCG.2021.3114827}
}
```
