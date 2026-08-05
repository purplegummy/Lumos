#!/usr/bin/env python3
"""Stress-test selection_bias_percentile against blind, no-strategy 10-teen picks.

Standalone dev tool -- NOT wired into the server, run directly:

    cd server && python3 stress_test_selection_percentile.py [--outer-trials N] [--repeats R]

PURPOSE
Quantify the false-positive rate: how often a purely random, no-strategy
10-teen selection gets flagged as "biased" (a high selection_bias_percentile)
under two prior conditions for screen_time_weekday. This reproduces at scale a
manual live-testing finding -- a blind 10-teen pick landing at the 99.4th
percentile under a spiked prior.

REPEATABILITY (--repeats R, default 30): a single 500-trial run's false-positive
rate can drift from nominal by chance, so each prior condition is run R times as
FULLY INDEPENDENT repeats (fresh blind samples + null draws; per-repeat seed =
base + r, so repeat #1 reproduces the original single run exactly). We report the
mean / std / min / max of the >0.90 and >0.95 rates ACROSS the R repeats (NOT
pooled into one grand rate), so a stable pattern is distinguishable from
one-run noise. --repeats 1 reproduces the original single-run behavior/output.

TWO PRIOR CONDITIONS (all other variables Uniform in both -> identical
diagnosed/nonDiagnosed counts -> js weight 0 -> they drop out, so DC is driven
entirely by screen_time_weekday):

  * spiked    -- Group A Condition 1: all 30 diagnosed tokens in the top
                 screen-time bin, all 30 nonDiagnosed tokens in the bottom bin
                 (maximally separated, extreme prior).
  * realistic -- DERIVED EMPIRICALLY from the real data (not eyeballed): the
                 actual screen_time_weekday histogram of diagnosed vs
                 nonDiagnosed teens over the same 9 UI bins, each scaled to 30
                 tokens. More rigorous than a hand-guessed "plausible" prior.

REUSE (no reimplementation): bias.read_data for loading (the same loader
server.py uses to populate DATA_MAP), and the existing UNMODIFIED metric
functions dc_metric.dc_map / selection_bias / selection_bias_percentile.
"""
import argparse
import random
import time

import numpy as np

import bias
import dc_metric
from bias import _bucket_numerical

DATASET = "mental_health_data.csv"
SCREEN = "screen_time_weekday"

# The 9 UI bins for screen_time_weekday: width-1 bins over the observed integer
# range 0..8 (data max is 8, so the top "8-9" bin holds value 8). 10 edges = 9
# bins. Verified empirically: values 0..8 bucket to indices 0..8.
SCREEN_BIN_EDGES = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
N_BINS = len(SCREEN_BIN_EDGES) - 1          # 9
TOKENS_PER_GROUP = 30                       # each elicitation group gets 30 balls
SELECT_K = 10                               # a submission is exactly 10 teens
INNER_TRIALS = 1000                         # null draws per percentile call (fixed)

# Distinct seeds so the outer sampling loop and the inner null sampling are
# independently reproducible (re-running with these seeds reproduces the run).
OUTER_SEED = 20260728                        # Python random, outer 10-teen picks
INNER_SEED = 917                             # numpy global, selection_bias_percentile

# Rough per-call cost, only for the printed runtime ESTIMATE (does not affect
# results): ~21s / 1000 selection_bias_percentile calls from the prior measured run.
SECONDS_PER_CALL_EST = 0.021

# Non-screen variables get a Uniform (weight-0) belief. Values only matter in
# that diagnosed == nonDiagnosed, which forces js_distance -> 0 so they drop out.
UNIFORM_NUMERIC = ["child_age_years", "hours_sleep_weeknight", "days_physical_activity_week"]
UNIFORM_CATEGORICAL = {
    "child_sex": ["Female", "Male"],
    "difficulty_making_friends":
        ["A little difficulty", "A lot of difficulty", "No difficulty"],
}


# --------------------------------------------------------------------------- #
# Belief builders (the contract dc_metric.dc_map consumes)
# --------------------------------------------------------------------------- #
def numeric_belief(attr, bin_edges, counts_d, counts_n):
    return {
        "attribute": attr,
        "binEdges": list(bin_edges),
        "countsByGroup": {
            "diagnosed": {"counts": list(counts_d), "confidence": 4.0},
            "nonDiagnosed": {"counts": list(counts_n), "confidence": 4.0},
        },
    }


def categorical_belief(attr, categories, counts_d, counts_n):
    return {
        "attribute": attr,
        "categories": list(categories),
        "countsByGroup": {
            "diagnosed": {"counts": list(counts_d), "confidence": 4.0},
            "nonDiagnosed": {"counts": list(counts_n), "confidence": 4.0},
        },
    }


def uniform_beliefs():
    """Weight-0 filler beliefs for every non-screen variable (diagnosed == non)."""
    beliefs = {}
    for attr in UNIFORM_NUMERIC:
        flat = [1] * N_BINS
        beliefs[attr] = numeric_belief(attr, SCREEN_BIN_EDGES, flat, flat)
    for attr, cats in UNIFORM_CATEGORICAL.items():
        flat = [1] * len(cats)
        beliefs[attr] = categorical_belief(attr, cats, flat, flat)
    return beliefs


def spiked_screen_belief():
    """All 30 diagnosed tokens in the top bin, all 30 nonDiagnosed in the bottom."""
    counts_d = [0] * N_BINS
    counts_d[-1] = TOKENS_PER_GROUP          # top "8-9" bin
    counts_n = [0] * N_BINS
    counts_n[0] = TOKENS_PER_GROUP           # bottom "0-1" bin
    return numeric_belief(SCREEN, SCREEN_BIN_EDGES, counts_d, counts_n)


def _scale_to_tokens(hist, tokens):
    """Scale a raw histogram to sum to `tokens`, preserving proportions (float)."""
    total = sum(hist)
    if total == 0:
        return [0.0] * len(hist)
    return [h / total * tokens for h in hist]


def realistic_screen_belief(data):
    """Empirical prior: real screen_time histogram per label group, scaled to 30.

    Returns (belief, (raw_diag_hist, raw_nond_hist)) so the raw histograms can be
    printed for transparency. The 30-token scale is immaterial to DC (js_distance
    / vba normalize) but mirrors the elicitation's per-group token budget.
    """
    diag_hist = [0] * N_BINS
    nond_hist = [0] * N_BINS
    for teen in data.values():
        idx = _bucket_numerical(SCREEN_BIN_EDGES, float(teen[SCREEN]))
        if str(teen[dc_metric.LABEL_ATTR]) == dc_metric.DIAGNOSED_VALUE:
            diag_hist[idx] += 1
        else:
            nond_hist[idx] += 1
    belief = numeric_belief(
        SCREEN, SCREEN_BIN_EDGES,
        _scale_to_tokens(diag_hist, TOKENS_PER_GROUP),
        _scale_to_tokens(nond_hist, TOKENS_PER_GROUP),
    )
    return belief, (diag_hist, nond_hist)


def build_dc_map(data, screen_belief):
    """dc_map over all 200 teens for {screen_belief} + the Uniform fillers."""
    beliefs = uniform_beliefs()
    beliefs[SCREEN] = screen_belief
    return dc_metric.dc_map(data, beliefs)


# --------------------------------------------------------------------------- #
# Stress loop
# --------------------------------------------------------------------------- #
def run_condition(dc_map, ids, outer_trials):
    """Blind 10-teen picks -> (raw selection_bias array, percentile array).

    Reseeds both streams up front so the two conditions are evaluated on the
    IDENTICAL sequence of blind selections and null draws (a paired, reproducible
    comparison). selection_bias_percentile is called with rng=None, so it draws
    from the numpy global we seed here.
    """
    outer_rng = random.Random(OUTER_SEED)    # distinct from the numpy inner stream
    np.random.seed(INNER_SEED)               # seeds selection_bias_percentile's null

    raw_vals = np.empty(outer_trials, dtype=float)
    pcts = np.empty(outer_trials, dtype=float)
    for t in range(outer_trials):
        # genuinely blind: uniform 10 distinct ids, NOT filtered/sorted by DC.
        sample = outer_rng.sample(ids, SELECT_K)
        raw_vals[t] = dc_metric.selection_bias(dc_map, sample)
        pcts[t] = dc_metric.selection_bias_percentile(dc_map, sample, n_trials=INNER_TRIALS)
    return raw_vals, pcts


def _set_repeat_seeds(outer_seed, inner_seed):
    """Re-point the module-level seeds that run_condition reads on entry, so each
    repeat gets a fresh, deterministic sampling stream WITHOUT modifying
    run_condition (it reseeds random.Random(OUTER_SEED) / np.random.seed(INNER_SEED)
    from these globals every call). Repeat #1 sets them back to the base values, so
    it reproduces the original run exactly.
    """
    global OUTER_SEED, INNER_SEED
    OUTER_SEED = outer_seed
    INNER_SEED = inner_seed


def summarize(arr):
    return {
        "mean": float(np.mean(arr)),
        "std": float(np.std(arr)),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
    }


def report_condition(name, screen_belief_desc, raw_vals, pcts):
    rs = summarize(raw_vals)
    ps = summarize(pcts)
    frac_90 = float(np.mean(pcts > 0.90))
    frac_95 = float(np.mean(pcts > 0.95))
    print(f"\n{'=' * 72}\nCONDITION: {name}\n{screen_belief_desc}\n{'-' * 72}")
    print(f"  raw selection_bias   mean={rs['mean']:+.4f}  std={rs['std']:.4f}  "
          f"min={rs['min']:+.4f}  max={rs['max']:+.4f}")
    print(f"  percentile           mean={ps['mean']:.4f}  std={ps['std']:.4f}  "
          f"min={ps['min']:.4f}  max={ps['max']:.4f}")
    print(f"  fraction percentile > 0.90 : {frac_90:.3f}  ({int(round(frac_90 * len(pcts)))}/{len(pcts)})")
    print(f"  fraction percentile > 0.95 : {frac_95:.3f}  ({int(round(frac_95 * len(pcts)))}/{len(pcts)})")
    return frac_90, frac_95


def _rate_stats(values):
    """(mean, sample-std, min, max) of a list of per-repeat rates. Sample std
    (ddof=1) characterizes the run-to-run spread; 0.0 when only one repeat."""
    a = np.asarray(values, dtype=float)
    return (
        float(np.mean(a)),
        float(np.std(a, ddof=1)) if a.size > 1 else 0.0,
        float(np.min(a)),
        float(np.max(a)),
    )


def _print_per_repeat_table(rows):
    """rows: list of (repeat_num, condition, frac_90, frac_95)."""
    print(f"\n{'=' * 72}\nPER-REPEAT FALSE-POSITIVE RATES "
          f"(each repeat = one independent outer-trials run)\n{'-' * 72}")
    print(f"  {'repeat':>6}  {'condition':<11}{'>0.90':>9}{'>0.95':>9}")
    for rep, cond, f90, f95 in rows:
        print(f"  {rep:>6}  {cond:<11}{f90:>9.3f}{f95:>9.3f}")
    print(f"{'=' * 72}")


def _print_summary_stats(rates, repeats):
    """rates: {condition: {'f90': [...], 'f95': [...]}} across the R repeats."""
    print(f"\n{'=' * 72}\nSUMMARY ACROSS {repeats} REPEATS  "
          f"(false-positive rate, per condition & threshold)\n{'-' * 72}")
    print(f"  {'condition':<11}{'threshold':<10}{'mean':>9}{'std':>9}{'min':>9}{'max':>9}")
    for cond in ("spiked", "realistic"):
        for thr_key, thr_label in (("f90", ">0.90"), ("f95", ">0.95")):
            mean, std, lo, hi = _rate_stats(rates[cond][thr_key])
            print(f"  {cond:<11}{thr_label:<10}{mean:>9.4f}{std:>9.4f}{lo:>9.3f}{hi:>9.3f}")
    print(f"{'=' * 72}")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--outer-trials", type=int, default=500,
                        help="number of blind 10-teen selections per condition, per repeat (default 500)")
    parser.add_argument("--repeats", type=int, default=30,
                        help="independent repeats of the full outer-trials run per condition, each with "
                             "a fresh derived seed (default 30). --repeats 1 reproduces the original "
                             "single-run behavior/output.")
    args = parser.parse_args()
    outer_trials = args.outer_trials
    repeats = args.repeats

    # Base seeds; each repeat r re-derives (base + r), so repeat #1 (r=0) reproduces
    # the ORIGINAL run exactly and every later repeat is a distinct, reproducible run.
    base_outer, base_inner = OUTER_SEED, INNER_SEED

    t_start = time.perf_counter()

    bias.read_data(DATASET)                  # reuse the server's loader
    data = bias.DATA_MAP[DATASET]["data"]
    ids = list(data.keys())                  # 200 full-name ids; dc_map is keyed the same
    print(f"Loaded {len(ids)} teens from {DATASET}. "
          f"outer_trials={outer_trials}, inner_trials={INNER_TRIALS}, "
          f"seeds(outer={OUTER_SEED}, inner={INNER_SEED})")

    # --- build the two priors + their dc_maps (once, shared by all repeats) --
    spiked = spiked_screen_belief()
    realistic, (diag_hist, nond_hist) = realistic_screen_belief(data)
    print(f"\nEmpirical screen_time_weekday histograms (bins 0..8, raw counts):")
    print(f"  diagnosed    (n={sum(diag_hist)}): {diag_hist}")
    print(f"  nonDiagnosed (n={sum(nond_hist)}): {nond_hist}")

    dc_spiked = build_dc_map(data, spiked)
    dc_realistic = build_dc_map(data, realistic)

    if repeats > 1:
        est = SECONDS_PER_CALL_EST * repeats * 2 * outer_trials
        print(f"\n{'=' * 72}\nREPEATABILITY MODE: {repeats} independent repeats x 2 conditions x "
              f"{outer_trials} trials\n{'-' * 72}")
        print(f"  per-repeat seeds = base + r  (outer={base_outer}+r, inner={base_inner}+r)")
        print(f"  estimated runtime ~{est:.0f}s (~{est / 60:.1f} min) at "
              f"~{SECONDS_PER_CALL_EST * 1000:.0f} ms/call; full per-condition detail shown for repeat #1 only")
        print(f"{'=' * 72}")

    # per-repeat false-positive rates, collected for the across-repeat summary
    rates = {"spiked": {"f90": [], "f95": []},
             "realistic": {"f90": [], "f95": []}}
    per_repeat_rows = []

    for r in range(repeats):
        _set_repeat_seeds(base_outer + r, base_inner + r)

        # blind sampling logic reused verbatim; both conditions share this repeat's
        # seed, so they stay a PAIRED comparison within the repeat.
        raw_s, pct_s = run_condition(dc_spiked, ids, outer_trials)
        raw_r, pct_r = run_condition(dc_realistic, ids, outer_trials)

        if r == 0:
            # repeat #1: full original-style detail as a representative example
            f90_s, f95_s = report_condition(
                "spiked", "screen prior: all 30 diagnosed in top bin, all 30 non in bottom bin",
                raw_s, pct_s)
            f90_r, f95_r = report_condition(
                "realistic", "screen prior: empirical per-group histogram, scaled to 30 tokens",
                raw_r, pct_r)
            # original single-run side-by-side table (identical output for --repeats 1)
            print(f"\n{'=' * 72}\nFALSE-POSITIVE RATE: blind 10-teen pick flagged as biased\n{'-' * 72}")
            print(f"  {'threshold':<14}{'spiked':>12}{'realistic':>14}")
            print(f"  {'percentile>0.90':<14}{f90_s:>12.3f}{f90_r:>14.3f}")
            print(f"  {'percentile>0.95':<14}{f95_s:>12.3f}{f95_r:>14.3f}")
            print(f"{'=' * 72}")
        else:
            # later repeats: the same fraction formula report_condition uses, no printing
            f90_s, f95_s = float(np.mean(pct_s > 0.90)), float(np.mean(pct_s > 0.95))
            f90_r, f95_r = float(np.mean(pct_r > 0.90)), float(np.mean(pct_r > 0.95))

        rates["spiked"]["f90"].append(f90_s)
        rates["spiked"]["f95"].append(f95_s)
        rates["realistic"]["f90"].append(f90_r)
        rates["realistic"]["f95"].append(f95_r)
        per_repeat_rows.append((r + 1, "spiked", f90_s, f95_s))
        per_repeat_rows.append((r + 1, "realistic", f90_r, f95_r))

    if repeats > 1:
        _print_per_repeat_table(per_repeat_rows)
        _print_summary_stats(rates, repeats)

    elapsed = time.perf_counter() - t_start
    print(f"total wall-clock runtime: {elapsed:.1f}s "
          f"({repeats * 2 * outer_trials} percentile calls x {INNER_TRIALS} null draws each)")


if __name__ == "__main__":
    main()
