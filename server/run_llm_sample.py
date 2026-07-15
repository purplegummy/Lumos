"""Run one sample signal JSON through the LLM intervention pipeline.

Offline path for validating the LLM input/output with no server, no socket and
no frontend attached. Reuses the exact same assembly and generation functions the
live path uses (llm_intervention.assemble_llm_input / generate_with_usage), so
what you see here is what a participant would get.

Usage (from the server/ directory, or anywhere -- the script adds its own dir to
the import path):

    python run_llm_sample.py llm_samples/sample_input_1_screen_time.json
    python run_llm_sample.py llm_samples/sample_input_1_screen_time.json --dry-run
    python run_llm_sample.py llm_samples/*.json          # shell glob, several at once

--dry-run prints only the assembled payload and never calls the API, so it works
with no ANTHROPIC_API_KEY set. Without --dry-run it calls Claude and prints the
structured output plus latency and token counts.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import llm_intervention


def run_one(path, dry_run, model, effort):
    """Assemble (and optionally generate) one sample. Returns a shell exit code."""
    with open(path, encoding="utf-8") as f:
        session = json.load(f)

    print("=" * 70)
    print(f"sample: {os.path.basename(path)}")
    if session.get("_description"):
        print(f"        {session['_description']}")
    print("=" * 70)

    llm_input = llm_intervention.assemble_llm_input(session)

    print("\n-- assembled input (what goes to the model) --------------------")
    print(json.dumps(llm_input, indent=2, ensure_ascii=False))

    if dry_run:
        print("\n(dry run: no API call)\n")
        return 0

    started = time.time()
    result, usage = llm_intervention.generate_with_usage(
        llm_input, model=model, effort=effort)
    elapsed = time.time() - started

    if result is None:
        print("\n-- no output ---------------------------------------------------")
        print("ANTHROPIC_API_KEY unset, or generation failed (see message above).")
        print(f"({elapsed:.1f}s)\n")
        return 1

    print("\n-- output ------------------------------------------------------")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    tokens = (f"in={usage['input_tokens']} out={usage['output_tokens']}"
              if usage else "n/a")
    print(f"\n({elapsed:.1f}s, tokens {tokens})\n")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Run sample signal JSON(s) through the LLM intervention pipeline.")
    parser.add_argument("samples", nargs="+",
                        help="path(s) to a sample JSON in llm_samples/")
    parser.add_argument("--dry-run", action="store_true",
                        help="assemble and print the payload only; no API call, no key needed")
    parser.add_argument("--model", default=llm_intervention.DEFAULT_MODEL,
                        help=f"model id (default: {llm_intervention.DEFAULT_MODEL})")
    parser.add_argument("--effort", default=llm_intervention.DEFAULT_EFFORT,
                        help=f"output effort (default: {llm_intervention.DEFAULT_EFFORT})")
    args = parser.parse_args()

    failures = 0
    for path in args.samples:
        failures += run_one(path, args.dry_run, args.model, args.effort)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
