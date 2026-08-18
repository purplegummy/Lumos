"""The pick-counted cooldown on the mid-task selection trigger.

    cd server && python3 test_selection_trigger.py

Only the cooldown is exercised. The bias decision itself is the documented seam
(evaluate_summary_trigger today, Lester's per-variable check next), so it is
stubbed out here rather than pinned down -- the point of the seam is that the
spacing around it holds whichever check is behind it.
"""
import llm_trigger


def main():
    failures = 0

    def check(label, cond):
        nonlocal failures
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        if not cond:
            failures += 1

    original = llm_trigger.evaluate_summary_trigger
    llm_trigger.evaluate_summary_trigger = lambda *_: (True, "ok")
    try:
        record = {}
        fired, _ = llm_trigger.evaluate_selection_trigger(record, {"n_selected": 5}, [])
        check("fires on the 5th pick with no history", fired)
        check("records the pick count it fired on",
              record["selection_last_fired_n"] == 5)

        fired, reason = llm_trigger.evaluate_selection_trigger(
            record, {"n_selected": 6}, [])
        check("one more pick is still on cooldown",
              not fired and reason.startswith("cooldown"))

        fired, _ = llm_trigger.evaluate_selection_trigger(record, {"n_selected": 7}, [])
        check(f"fires again after {llm_trigger.SELECTION_RECHECK_PICKS} more picks",
              fired and record["selection_last_fired_n"] == 7)
    finally:
        llm_trigger.evaluate_summary_trigger = original

    print("\n" + "=" * 72)
    print(f"{'ALL CHECKS PASSED' if failures == 0 else str(failures) + ' CHECK(S) FAILED'}")
    print("=" * 72)
    return failures


if __name__ == "__main__":
    import sys
    sys.exit(1 if main() else 0)
