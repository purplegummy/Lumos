#!/usr/bin/env python3
"""Stress-test dwell_bias_percentile against blind, no-strategy dwell sessions.

Standalone dev tool -- NOT wired into the server, run directly:

    cd server && python3 stress_test_dwell_percentile.py [--outer-trials N] [--total-dwell-ms MS]

PURPOSE
The dwell-side twin of stress_test_selection_percentile.py: quantify how often
a blind, no-strategy participant is flagged as biased (high dwell_bias_percentile)
under a spiked vs. an empirically-derived realistic screen_time_weekday prior.
Closes the gap where only selection_bias had been calibration-checked.

REUSE (no duplicated setup): imports the dataset loader access, the two prior
conditions (spiked / realistic -- the exact empirical derivation), the Uniform
filler beliefs, and summarize() from stress_test_selection_percentile. Only the
detailed-map build and the blind-dwell modeling are new here. The existing
dc_metric functions (dwell_bias, dwell_bias_percentile, sample_null_dwell_bias)
are called UNMODIFIED.

MODELING A "BLIND" DWELL SESSION
A no-strategy participant has no strategy in EITHER dimension:
  1. which 10 teens they touch  -> uniform random 10 distinct ids (not DC-sorted)
  2. how long they dwell on each -> proportions from dirichlet(ones(10)) * a total
     budget -- the SAME mechanism sample_null_dwell_bias uses. A no-strategy
     participant has no more reason to dwell deliberately on any one teen than to
     have picked them deliberately.

total_dwell_ms is an ASSUMPTION (see report), default 180000 ms = 3 min, matching
the trigger gate's minimum elapsed-time threshold -- i.e. a "typical session at the
moment the gate first opens." It is NOT empirically grounded.

TIE DIAGNOSTIC
The selection stress test surfaced tie-deflation of the strict-`<` percentile under
the spiked prior (repeated identical DC values). Here we measure, per trial, the
fraction of the 1000 null draws within 1e-9 of the real value, and report its mean
per condition -- to see whether continuous Dirichlet dwell times make exact ties
rare, or whether the spiked prior's degenerate DC still produces them.
"""
import argparse
import random
import time

import numpy as np

import bias
import dc_metric
import stress_test_selection_percentile as sel

# Separate, distinct seeds so every random stream is independently reproducible:
OUTER_SEED = 20260728     # Python random: which 10 teens the blind participant touches
OBS_DWELL_SEED = 4242     # numpy Generator: the observed participant's dwell split
INNER_SEED = 917          # numpy global: dwell_bias_percentile's internal null (rng=None)
TIE_SEED = 13             # numpy Generator: the tie-diagnostic null (kept off the global stream)

TIE_EPS = 1e-9
K = sel.SELECT_K          # 10 teens per session
INNER_TRIALS = sel.INNER_TRIALS   # 1000 null draws per percentile call


def build_detailed_map(data, screen_belief):
    """dc_map_DETAILED over all 200 teens for {screen_belief} + Uniform fillers.

    dwell_bias needs the detailed map (per-teen dc/consistency/weights), not the
    scalar dc_map. Reuses the exact belief set the selection script builds.
    """
    beliefs = sel.uniform_beliefs()
    beliefs[sel.SCREEN] = screen_belief
    return dc_metric.dc_map_detailed(data, beliefs)


def run_condition(detailed_map, ids, outer_trials, total_dwell_ms):
    """Blind dwell sessions -> (raw dwell_bias, percentile, tie-fraction) arrays.

    Reseeds all streams up front so both conditions see the IDENTICAL sequence of
    blind sessions (same teen sets AND same dwell splits) and null draws -- a
    paired, reproducible comparison. dwell_bias_percentile runs with rng=None so it
    draws from the numpy global we seed here; the tie-diagnostic null uses its own
    generator so it never perturbs that global stream.
    """
    outer_rng = random.Random(OUTER_SEED)           # teen selection
    obs_rng = np.random.default_rng(OBS_DWELL_SEED)  # observed dwell split
    tie_rng = np.random.default_rng(TIE_SEED)        # tie-diagnostic null
    np.random.seed(INNER_SEED)                       # dwell_bias_percentile's null

    raw_vals = np.empty(outer_trials, dtype=float)
    pcts = np.empty(outer_trials, dtype=float)
    tie_fracs = np.empty(outer_trials, dtype=float)

    for t in range(outer_trials):
        # blind: uniform 10 distinct ids, then a random (no-strategy) dwell split.
        sample = outer_rng.sample(ids, K)
        props = obs_rng.dirichlet(np.ones(K))
        dwell = {sample[i]: float(props[i] * total_dwell_ms) for i in range(K)}

        real = dc_metric.dwell_bias(detailed_map, dwell)                    # existing
        pcts[t] = dc_metric.dwell_bias_percentile(                         # existing
            detailed_map, dwell, n_trials=INNER_TRIALS)
        raw_vals[t] = real

        # Tie diagnostic: a fresh independent null of the SAME distribution (the
        # percentile fn doesn't expose its own array); statistically equivalent for
        # estimating the mean tie fraction. Same k / total_dwell so the null matches.
        null = dc_metric.sample_null_dwell_bias(
            detailed_map, K, sum(dwell.values()), n_trials=INNER_TRIALS, rng=tie_rng)
        tie_fracs[t] = float((np.abs(null - real) <= TIE_EPS).mean())

    return raw_vals, pcts, tie_fracs


def report_condition(name, desc, raw_vals, pcts, tie_fracs):
    rs = sel.summarize(raw_vals)
    ps = sel.summarize(pcts)
    frac_90 = float(np.mean(pcts > 0.90))
    frac_95 = float(np.mean(pcts > 0.95))
    mean_tie = float(np.mean(tie_fracs))
    n = len(pcts)
    print(f"\n{'=' * 72}\nCONDITION: {name}\n{desc}\n{'-' * 72}")
    print(f"  raw dwell_bias       mean={rs['mean']:+.4f}  std={rs['std']:.4f}  "
          f"min={rs['min']:+.4f}  max={rs['max']:+.4f}")
    print(f"  percentile           mean={ps['mean']:.4f}  std={ps['std']:.4f}  "
          f"min={ps['min']:.4f}  max={ps['max']:.4f}")
    print(f"  fraction percentile > 0.90 : {frac_90:.3f}  ({int(round(frac_90 * n))}/{n})")
    print(f"  fraction percentile > 0.95 : {frac_95:.3f}  ({int(round(frac_95 * n))}/{n})")
    print(f"  mean tie-fraction (null within {TIE_EPS:g} of real) : {mean_tie:.5f}")
    return frac_90, frac_95, mean_tie


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--outer-trials", type=int, default=500,
                        help="blind dwell sessions per condition (default 500)")
    parser.add_argument("--total-dwell-ms", type=int, default=180000,
                        help="assumed total dwell budget per session, ms (default 180000 = 3 min)")
    args = parser.parse_args()
    outer_trials = args.outer_trials
    total_dwell_ms = args.total_dwell_ms

    t_start = time.perf_counter()

    bias.read_data(sel.DATASET)                 # reuse the server's loader
    data = bias.DATA_MAP[sel.DATASET]["data"]
    ids = list(data.keys())                     # 200 full-name ids; maps are keyed the same
    print(f"Loaded {len(ids)} teens from {sel.DATASET}. "
          f"outer_trials={outer_trials}, inner_trials={INNER_TRIALS}, "
          f"total_dwell_ms={total_dwell_ms} (ASSUMED 3-min session), "
          f"seeds(outer={OUTER_SEED}, obs_dwell={OBS_DWELL_SEED}, inner={INNER_SEED}, tie={TIE_SEED})")

    # --- build the two priors + their DETAILED dc_maps ----------------------
    spiked = sel.spiked_screen_belief()
    realistic, (diag_hist, nond_hist) = sel.realistic_screen_belief(data)
    print(f"\nEmpirical screen_time_weekday histograms (bins 0..8, raw counts):")
    print(f"  diagnosed    (n={sum(diag_hist)}): {diag_hist}")
    print(f"  nonDiagnosed (n={sum(nond_hist)}): {nond_hist}")

    dm_spiked = build_detailed_map(data, spiked)
    dm_realistic = build_detailed_map(data, realistic)

    # --- run both stress tests ---------------------------------------------
    raw_s, pct_s, tie_s = run_condition(dm_spiked, ids, outer_trials, total_dwell_ms)
    f90_s, f95_s, t_s = report_condition(
        "spiked", "screen prior: all 30 diagnosed in top bin, all 30 non in bottom bin",
        raw_s, pct_s, tie_s)

    raw_r, pct_r, tie_r = run_condition(dm_realistic, ids, outer_trials, total_dwell_ms)
    f90_r, f95_r, t_r = report_condition(
        "realistic", "screen prior: empirical per-group histogram, scaled to 30 tokens",
        raw_r, pct_r, tie_r)

    elapsed = time.perf_counter() - t_start

    # --- side-by-side comparison (same format as the selection script) ------
    print(f"\n{'=' * 72}\nFALSE-POSITIVE RATE: blind dwell session flagged as biased\n{'-' * 72}")
    print(f"  {'threshold':<16}{'spiked':>12}{'realistic':>14}")
    print(f"  {'percentile>0.90':<16}{f90_s:>12.3f}{f90_r:>14.3f}")
    print(f"  {'percentile>0.95':<16}{f95_s:>12.3f}{f95_r:>14.3f}")
    print(f"  {'mean tie-frac':<16}{t_s:>12.5f}{t_r:>14.5f}")
    print(f"{'=' * 72}")
    print(f"total wall-clock runtime: {elapsed:.1f}s "
          f"({2 * outer_trials} percentile calls + {2 * outer_trials} tie-diagnostic nulls, "
          f"{INNER_TRIALS} draws each)")


if __name__ == "__main__":
    main()
