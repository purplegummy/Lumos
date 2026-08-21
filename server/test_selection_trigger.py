"""The fixed pick schedule on the mid-task selection trigger.

    cd server && python3 test_selection_trigger.py

Only the schedule is exercised. The bias decision itself belongs to
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

    calls = []
    original = llm_trigger.evaluate_selection_progressive_trigger

    def stub(_record, ids, fired=True):
        calls.append(len(set(ids)))
        return {"ready": True, "reason": "ok", "fired": fired,
                "target_var": "screen_time_weekday" if fired else None,
                "target_percentile": 0.99 if fired else None,
                "n_selected": len(set(ids)), "percentile_by_var": {}}

    llm_trigger.evaluate_selection_progressive_trigger = stub
    try:
        record = {}
        picks = [f"t{i}" for i in range(1, 11)]

        result = llm_trigger.evaluate_selection_trigger(record, picks[:5])
        check("checks (and here fires) at the 5th pick", result["fired"])
        check("hands the pinned variable through",
              result["target_var"] == "screen_time_weekday")

        result = llm_trigger.evaluate_selection_trigger(record, picks[:6])
        check("6th pick is off the schedule",
              not result.get("fired") and result["reason"].startswith("off_schedule"))

        result = llm_trigger.evaluate_selection_trigger(record, picks[:7])
        check("checks again at the 7th even though the 5th fired", result["fired"])

        result = llm_trigger.evaluate_selection_trigger(record, picks[:8])
        check("8th pick is off the schedule",
              result["reason"].startswith("off_schedule"))

        result = llm_trigger.evaluate_selection_trigger(record, picks[:9])
        check("checks at the 9th", result["fired"])
        check("evaluation ran exactly at 5, 7, 9", calls == [5, 7, 9])

        # deselect below a consumed checkpoint and re-cross it: no re-check --
        # reshuffling an already-checked selection is not two picks of new evidence
        result = llm_trigger.evaluate_selection_trigger(record, picks[:8])
        result = llm_trigger.evaluate_selection_trigger(record, picks[:9])
        check("re-crossing a consumed checkpoint does not re-check",
              result["reason"].startswith("already_checked") and calls == [5, 7, 9])

        # the schedule is not a post-fire cooldown: a non-fire consumes its
        # checkpoint too, and the next check still happens two picks later
        record2 = {}
        llm_trigger.evaluate_selection_progressive_trigger = \
            lambda r, ids: stub(r, ids, fired=False)
        result = llm_trigger.evaluate_selection_trigger(record2, picks[:5])
        check("a checked non-fire still reports ready", result["ready"] and not result["fired"])
        result = llm_trigger.evaluate_selection_trigger(record2, picks[:5])
        check("its checkpoint is consumed all the same",
              result["reason"].startswith("already_checked"))
    finally:
        llm_trigger.evaluate_selection_progressive_trigger = original

    print("\n" + "=" * 72)
    print(f"{'ALL CHECKS PASSED' if failures == 0 else str(failures) + ' CHECK(S) FAILED'}")
    print("=" * 72)
    return failures


if __name__ == "__main__":
    import sys
    sys.exit(1 if main() else 0)
