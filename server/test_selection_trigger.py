"""The pick-counted cooldown on the mid-task selection trigger.

    cd server && python3 test_selection_trigger.py

Only the cooldown is exercised. The bias decision itself belongs to
evaluate_selection_progressive_trigger (which has its own test file), so it is
stubbed out here -- the point of the seam is that the spacing around it holds
whichever check is behind it.
"""
import llm_trigger


def main():
    failures = 0

    def check(label, cond):
        nonlocal failures
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        if not cond:
            failures += 1

    original = llm_trigger.evaluate_selection_progressive_trigger
    llm_trigger.evaluate_selection_progressive_trigger = lambda _record, ids: {
        "ready": True, "reason": "ok", "fired": True,
        "target_var": "screen_time_weekday", "target_percentile": 0.99,
        "n_selected": len(set(ids)), "percentile_by_var": {}}
    try:
        record = {}
        picks = [f"t{i}" for i in range(1, 8)]
        result = llm_trigger.evaluate_selection_trigger(record, picks[:5])
        check("fires on the 5th pick with no history", result["fired"])
        check("hands the pinned variable through",
              result["target_var"] == "screen_time_weekday")
        check("records the pick count it fired on",
              record["selection_last_fired_n"] == 5)

        result = llm_trigger.evaluate_selection_trigger(record, picks[:6])
        check("one more pick is still on cooldown",
              not result.get("fired") and result["reason"].startswith("cooldown"))

        result = llm_trigger.evaluate_selection_trigger(record, picks[:7])
        check(f"fires again after {llm_trigger.SELECTION_RECHECK_PICKS} more picks",
              result["fired"] and record["selection_last_fired_n"] == 7)
    finally:
        llm_trigger.evaluate_selection_progressive_trigger = original

    print("\n" + "=" * 72)
    print(f"{'ALL CHECKS PASSED' if failures == 0 else str(failures) + ' CHECK(S) FAILED'}")
    print("=" * 72)
    return failures


if __name__ == "__main__":
    import sys
    sys.exit(1 if main() else 0)
