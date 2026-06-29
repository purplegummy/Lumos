"""Synthetic validation harness for dc_metric.py.

Standalone -- no real elicitation, no sockets, no live server path. Run it as:

    cd server && python3 test_dc_metric.py        # needs numpy + scipy (the venv)

It loads the real 200-teen mental_health_data.csv (so teen values + the
ever_diagnosed_dep_or_anx label are real), constructs SYNTHETIC beliefs in the
exact input contract, and checks the metric behaves directionally:

  * interacting only with belief-CONFIRMING teens  -> real_time_bias /
    overall_interaction_bias clearly POSITIVE
  * interacting only with belief-CONTRADICTING teens -> clearly NEGATIVE
  * a representative spread of teens                 -> ~ZERO
  * a variable whose two distributions are identical -> weight ~0 (drops out)
  * all-identical beliefs                            -> DC 0.0, no 0/0

It also prints the DC distribution summary and every phase metric for eyeballing.

Pure asserts + prints; exits non-zero on failure.
"""
import csv
import os
import random

import numpy as np

import dc_metric as dc

DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "mental_health_data.csv")

NUMERIC_ATTRS = [
    "child_age_years",
    "screen_time_weekday",
    "hours_sleep_weeknight",
    "days_physical_activity_week",
]


# --------------------------------------------------------------------------- #
# Load the real teens into the active_data idiom: {id: {attr: value, ...}}
# --------------------------------------------------------------------------- #
def load_teens():
    teens = {}
    with open(DATA_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tid = row["id"]
            teen = dict(row)
            for a in NUMERIC_ATTRS:
                teen[a] = float(teen[a])
            teens[tid] = teen
    return teens


def equal_width_edges(teens, attr, n_bins=10):
    """10 equal-width bin edges over the attribute's observed range (N+1 edges)."""
    vals = [teens[t][attr] for t in teens]
    lo, hi = min(vals), max(vals)
    step = (hi - lo) / n_bins
    return [lo + i * step for i in range(n_bins + 1)]


def q_belief(teens, attr, counts_d, counts_n, conf_d=4.0, conf_n=4.0):
    """A numerical variable belief in the contract shape."""
    return {
        "attribute": attr,
        "binEdges": equal_width_edges(teens, attr, len(counts_d)),
        "countsByGroup": {
            "diagnosed": {"counts": counts_d, "confidence": conf_d},
            "nonDiagnosed": {"counts": counts_n, "confidence": conf_n},
        },
    }


def cat_belief(attr, categories, counts_d, counts_n, conf_d=4.0, conf_n=4.0):
    """A categorical variable belief in the contract shape."""
    return {
        "attribute": attr,
        "categories": categories,
        "countsByGroup": {
            "diagnosed": {"counts": counts_d, "confidence": conf_d},
            "nonDiagnosed": {"counts": counts_n, "confidence": conf_n},
        },
    }


# --------------------------------------------------------------------------- #
# Build synthetic beliefs: 2 STRONGLY separated variables + 4 identical (w~0).
# --------------------------------------------------------------------------- #
def build_beliefs(teens):
    # Strong: diagnosed teens believed to have HIGH screen time, non-diagnosed LOW.
    screen = q_belief(
        teens,
        "screen_time_weekday",
        counts_d=[0, 0, 0, 1, 2, 3, 5, 7, 7, 5],   # mass in high bins
        counts_n=[5, 7, 7, 5, 3, 2, 1, 0, 0, 0],   # mass in low bins
        conf_d=5.0, conf_n=5.0,
    )
    # Strong: diagnosed teens believed to have LOW physical activity, non HIGH.
    activity = q_belief(
        teens,
        "days_physical_activity_week",
        counts_d=[6, 7, 6, 4, 3, 2, 1, 1, 0, 0],   # mass in low bins
        counts_n=[0, 0, 1, 1, 2, 3, 4, 6, 7, 6],   # mass in high bins
        conf_d=5.0, conf_n=5.0,
    )
    # Null variables: identical diagnosed/non distributions => js_distance 0 => drop out.
    flat10 = [3, 3, 3, 3, 3, 3, 3, 3, 3, 3]
    age = q_belief(teens, "child_age_years", list(flat10), list(flat10))
    sleep = q_belief(teens, "hours_sleep_weeknight", list(flat10), list(flat10))
    sex = cat_belief("child_sex", ["Female", "Male"], [15, 15], [15, 15])
    friends = cat_belief(
        "difficulty_making_friends",
        ["A little difficulty", "A lot of difficulty", "No difficulty"],
        [10, 10, 10], [10, 10, 10],
    )
    # Keyed by attribute (the contract's "dict of 6 variables").
    return {b["attribute"]: b for b in (screen, activity, age, sleep, sex, friends)}


def expected_cat_consistency(belief, value, diagnosed):
    """Hand-formula expected C for a categorical belief, keyed on the LABEL.

    A = vba(diagnosed, nonDiagnosed); idx = position of `value` in THIS belief's
    own `categories` array; y_d = +1 if diagnosed else -1; C = A[idx] * y_d.
    """
    cbg = belief["countsByGroup"]
    associations = dc.vba(cbg["diagnosed"]["counts"], cbg["nonDiagnosed"]["counts"])
    idx = belief["categories"].index(value)  # label -> position in this belief
    y_d = 1.0 if diagnosed else -1.0
    return associations[idx] * y_d


def make_teen(sex, diagnosed):
    """Minimal teen dict sufficient for consistency_for_teen on child_sex."""
    return {"child_sex": sex, dc.LABEL_ATTR: "Yes" if diagnosed else "No"}


def summarize(name, values):
    arr = np.asarray(values, dtype=float)
    print(
        f"  {name}: n={arr.size} mean={arr.mean():+.4f} std={arr.std():.4f} "
        f"min={arr.min():+.4f} q25={np.percentile(arr,25):+.4f} "
        f"med={np.median(arr):+.4f} q75={np.percentile(arr,75):+.4f} "
        f"max={arr.max():+.4f}"
    )


def main():
    random.seed(7)
    teens = load_teens()
    beliefs = build_beliefs(teens)

    print("=" * 72)
    print("DC METRIC -- synthetic validation harness")
    print("=" * 72)
    print(f"teens loaded: {len(teens)}  (from {os.path.relpath(DATA_PATH)})")
    n_diag = sum(1 for t in teens.values() if t[dc.LABEL_ATTR] == "Yes")
    print(f"actual label balance: diagnosed(Yes)={n_diag}  non(No)={len(teens)-n_diag}")

    # --- per-variable weights (conviction) ----------------------------------
    print("\nVariable weights  w_v = js_distance(diagnosed, nonDiagnosed):")
    weights = {}
    for attr, vb in beliefs.items():
        w = dc._variable_weight(vb)
        weights[attr] = w
        print(f"  {attr:30s} w={w:.4f}")

    # --- DC map -------------------------------------------------------------
    dcs = dc.dc_map(teens, beliefs)
    dc_vals = list(dcs.values())
    dc_std = float(np.std(dc_vals))
    print("\nDC distribution over all teens:")
    summarize("DC", dc_vals)

    # Partition teens by their DC:
    #   consistent   = top quartile (most belief-confirming)
    #   inconsistent = bottom quartile (most belief-contradicting)
    #   representative = systematic sample across the whole sorted range (mean ~ overall)
    sorted_ids = sorted(dcs, key=lambda i: dcs[i])
    n = len(sorted_ids)
    q = n // 4
    inconsistent_ids = sorted_ids[:q]
    consistent_ids = sorted_ids[-q:]
    representative_ids = sorted_ids[::2]  # every other teen across the range

    print(f"\nsubset sizes: consistent={len(consistent_ids)} "
          f"inconsistent={len(inconsistent_ids)} representative={len(representative_ids)}")

    # --- phase metrics ------------------------------------------------------
    rt_consistent = dc.real_time_bias(dcs, consistent_ids)
    rt_inconsistent = dc.real_time_bias(dcs, inconsistent_ids)
    rt_representative = dc.real_time_bias(dcs, representative_ids)
    oi_consistent = dc.overall_interaction_bias(dcs, consistent_ids)
    oi_inconsistent = dc.overall_interaction_bias(dcs, inconsistent_ids)
    sel_consistent = dc.selection_bias(dcs, consistent_ids)
    sel_inconsistent = dc.selection_bias(dcs, inconsistent_ids)
    fb_toward = dc.filter_bias(dcs, sorted_ids, consistent_ids)      # narrow to confirming
    fb_away = dc.filter_bias(dcs, sorted_ids, inconsistent_ids)      # narrow to contradicting

    print("\nPhase metrics (baseline = ALL 200 teens):")
    print(f"  real_time_bias        consistent  = {rt_consistent:+.4f}")
    print(f"  real_time_bias        inconsistent= {rt_inconsistent:+.4f}")
    print(f"  real_time_bias        representative={rt_representative:+.4f}")
    print(f"  overall_interaction   consistent  = {oi_consistent:+.4f}")
    print(f"  overall_interaction   inconsistent= {oi_inconsistent:+.4f}")
    print(f"  selection_bias        consistent  = {sel_consistent:+.4f}")
    print(f"  selection_bias        inconsistent= {sel_inconsistent:+.4f}")
    print(f"  filter_bias  all->consistent       = {fb_toward:+.4f}")
    print(f"  filter_bias  all->inconsistent     = {fb_away:+.4f}")

    # --- SANITY CHECKS ------------------------------------------------------
    print("\nSANITY CHECKS:")
    failures = 0

    def check(label, cond):
        nonlocal failures
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        if not cond:
            failures += 1

    # Directional: confirming -> positive, contradicting -> negative, rep -> ~0.
    check("real_time_bias positive for belief-consistent teens",
          rt_consistent > 0.2 * dc_std)
    check("real_time_bias negative for belief-inconsistent teens",
          rt_inconsistent < -0.2 * dc_std)
    check("real_time_bias ~0 for representative teens",
          abs(rt_representative) < 0.1 * dc_std)
    check("overall_interaction_bias positive for consistent teens",
          oi_consistent > 0.2 * dc_std)
    check("overall_interaction_bias negative for inconsistent teens",
          oi_inconsistent < -0.2 * dc_std)
    check("selection_bias positive for consistent, negative for inconsistent",
          sel_consistent > 0 and sel_inconsistent < 0)
    check("filter_bias positive narrowing toward consistent, negative toward inconsistent",
          fb_toward > 0 and fb_away < 0)

    # Strong variables carry real weight; null variables carry ~0.
    check("strong variables have high weight (screen & activity > 0.3)",
          weights["screen_time_weekday"] > 0.3 and weights["days_physical_activity_week"] > 0.3)
    check("identical-distribution variables have weight ~0 (drop out)",
          max(weights["child_age_years"], weights["hours_sleep_weeknight"],
              weights["child_sex"], weights["difficulty_making_friends"]) < 1e-9)

    # js_distance core: identical -> 0, different -> larger; bounded [0,1].
    check("js_distance(x, x) == 0", dc.js_distance([1, 2, 3, 4], [1, 2, 3, 4]) < 1e-12)
    check("js_distance increases with separation",
          dc.js_distance([10, 0, 0], [0, 0, 10]) > dc.js_distance([10, 0, 0], [5, 0, 5]))
    check("js_distance in [0,1]",
          0.0 <= dc.js_distance([10, 0], [0, 10]) <= 1.0)

    # vba sign convention: bin heavy on diagnosed -> A>0; on non -> A<0.
    a = dc.vba([10, 0], [0, 10])
    check("vba: diagnosed-heavy bin A>0, non-heavy bin A<0", a[0] > 0 and a[1] < 0)

    # All-identical beliefs -> every w=0 -> DC 0.0 for all, no divide-by-zero.
    flat = [3, 3, 3, 3, 3, 3, 3, 3, 3, 3]
    identical_beliefs = {
        attr: q_belief(teens, attr, list(flat), list(flat))
        for attr in NUMERIC_ATTRS
    }
    dcs_identical = dc.dc_map(teens, identical_beliefs)
    check("all-identical beliefs -> DC == 0.0 for every teen (no 0/0)",
          all(v == 0.0 for v in dcs_identical.values()))

    # Empty-subset guards.
    check("empty interaction set -> real_time_bias == 0.0",
          dc.real_time_bias(dcs, []) == 0.0)
    check("empty visible_after -> filter_bias == 0.0",
          dc.filter_bias(dcs, sorted_ids, []) == 0.0)

    # ======================================================================= #
    # CATEGORICAL-PATH CHECKS (exercise consistency_for_teen's category index)
    # ======================================================================= #
    TOL = 1e-9
    print("\n" + "-" * 72)
    print("CATEGORICAL PATH")
    print("-" * 72)

    # --- (1) categorical variable with w>0 participates in aggregate DC ------
    # Reuse the strong Q vars but swap child_sex to a STRONGLY separated belief
    # (diagnosed-heavy on Female), so its weight is clearly > 0 and it steers DC.
    beliefs_cat = dict(beliefs)
    beliefs_cat["child_sex"] = cat_belief(
        "child_sex", ["Female", "Male"], [25, 5], [5, 25], conf_d=5.0, conf_n=5.0
    )
    w_sex = dc._variable_weight(beliefs_cat["child_sex"])
    print(f"\nchild_sex weight with strong separation: w={w_sex:.4f}")

    dcs_cat = dc.dc_map(teens, beliefs_cat)
    dc_cat_std = float(np.std(list(dcs_cat.values())))
    sorted_cat = sorted(dcs_cat, key=lambda i: dcs_cat[i])
    qc = len(sorted_cat) // 4
    cat_inconsistent = sorted_cat[:qc]
    cat_consistent = sorted_cat[-qc:]
    cat_representative = sorted_cat[::2]
    rt_cat_consistent = dc.real_time_bias(dcs_cat, cat_consistent)
    rt_cat_inconsistent = dc.real_time_bias(dcs_cat, cat_inconsistent)
    rt_cat_representative = dc.real_time_bias(dcs_cat, cat_representative)
    print(f"  real_time_bias consistent   = {rt_cat_consistent:+.4f}")
    print(f"  real_time_bias inconsistent = {rt_cat_inconsistent:+.4f}")
    print(f"  real_time_bias representative= {rt_cat_representative:+.4f}")

    check("categorical variable child_sex has non-zero weight", w_sex > 0.3)
    check("direction still holds with categorical contributing (consistent>0)",
          rt_cat_consistent > 0.2 * dc_cat_std)
    check("direction still holds with categorical contributing (inconsistent<0)",
          rt_cat_inconsistent < -0.2 * dc_cat_std)
    check("direction still holds with categorical contributing (representative~0)",
          abs(rt_cat_representative) < 0.1 * dc_cat_std)

    # --- (2) hand-checked categorical consistency (precise, not just sign) ---
    # child_sex ["Female","Male"], diagnosed-heavy on Female -> A[Female]>0, A[Male]<0.
    hand = cat_belief("child_sex", ["Female", "Male"], [25, 5], [5, 25])
    A_hand = dc.vba([25, 5], [5, 25])
    print(f"\nhand belief child_sex ['Female','Male'] counts d=[25,5] n=[5,25]")
    print(f"  A[Female]={A_hand[0]:+.6f}  A[Male]={A_hand[1]:+.6f}")

    cases = [
        # (sex, diagnosed, expect_sign_label)
        ("Female", True, "POSITIVE (A[Female]>0 x +1)"),
        ("Male", False, "POSITIVE (A[Male]<0 x -1)"),
        ("Female", False, "NEGATIVE (A[Female]>0 x -1)"),
    ]
    for sex, diagnosed, label in cases:
        teen = make_teen(sex, diagnosed)
        actual = dc.consistency_for_teen(teen, hand)
        expected = expected_cat_consistency(hand, sex, diagnosed)
        print(f"  teen sex={sex:6s} diagnosed={str(diagnosed):5s} -> "
              f"expected={expected:+.6f} actual={actual:+.6f}  [{label}]")
        check(f"consistency matches hand value for sex={sex}, diagnosed={diagnosed}",
              abs(actual - expected) < TOL)
    # explicit sign checks as stated in the task
    check("Female+diagnosed -> positive",
          dc.consistency_for_teen(make_teen("Female", True), hand) > 0)
    check("Male+non-diagnosed -> positive",
          dc.consistency_for_teen(make_teen("Male", False), hand) > 0)
    check("Female+non-diagnosed -> negative",
          dc.consistency_for_teen(make_teen("Female", False), hand) < 0)

    # --- (3) category-order robustness: lookup keys on LABEL, not position ----
    # Same counts, deliberately swapped category order. Same teen (Female,
    # diagnosed) must track the LABEL: with order ["Female","Male"] Female is the
    # diagnosed-heavy bin (A>0 -> C>0); with order ["Male","Female"] the diagnosed
    # -heavy counts now sit on Male, so Female maps to the OTHER entry (A<0 -> C<0).
    belief_FM = cat_belief("child_sex", ["Female", "Male"], [25, 5], [5, 25])
    belief_MF = cat_belief("child_sex", ["Male", "Female"], [25, 5], [5, 25])
    teen_fd = make_teen("Female", True)
    c_FM = dc.consistency_for_teen(teen_fd, belief_FM)
    c_MF = dc.consistency_for_teen(teen_fd, belief_MF)
    exp_FM = expected_cat_consistency(belief_FM, "Female", True)
    exp_MF = expected_cat_consistency(belief_MF, "Female", True)
    print(f"\norder robustness (same teen Female+diagnosed, same counts d=[25,5] n=[5,25]):")
    print(f"  categories ['Female','Male'] -> expected={exp_FM:+.6f} actual={c_FM:+.6f}")
    print(f"  categories ['Male','Female'] -> expected={exp_MF:+.6f} actual={c_MF:+.6f}")
    check("order-FM consistency matches hand value", abs(c_FM - exp_FM) < TOL)
    check("order-MF consistency matches hand value", abs(c_MF - exp_MF) < TOL)
    check("swapping category order flips the result (keys on label, not position)",
          c_FM > 0 and c_MF < 0)

    print("\n" + "=" * 72)
    print(f"{'ALL CHECKS PASSED' if failures == 0 else str(failures) + ' CHECK(S) FAILED'}")
    print("=" * 72)
    return failures


if __name__ == "__main__":
    import sys
    sys.exit(1 if main() else 0)
