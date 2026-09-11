"""
Train (or retrain) the skip-risk model and print a model card.

    cd backend
    .venv\\Scripts\\python.exe scripts/train_habit_model.py

The API trains this automatically on first use, so you only need to run it
manually when you have changed the feature set, changed the synthetic
generator, or want the model card printed for the report.

Read the warning it prints. The metrics are measured on held-out SYNTHETIC
users, which validates that the pipeline learns the relationship it was given.
It says nothing about real human behaviour, and the report must not imply
otherwise.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.engines import habit_model  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Train the habit skip-risk model")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()

    print("Training on the synthetic population...")
    metrics = habit_model.train(save=not args.no_save, seed=args.seed)

    print("\n" + "=" * 62)
    print("  MODEL CARD - skip-risk classifier")
    print("=" * 62)
    for k, v in metrics.items():
        print(f"  {k:<16} {v}")
    print("-" * 62)
    print("  Features:")
    for f in habit_model.FEATURES:
        print(f"    - {f:<22} ({habit_model.FEATURE_LABELS[f]})")
    print("=" * 62)
    print(
        "\n  WARNING: these metrics are measured on held-out SYNTHETIC users\n"
        "  drawn from the documented rule in habit_model._latent_skip_logit.\n"
        "  They show the pipeline recovers a known relationship. They are NOT\n"
        "  evidence about real adherence. Retrain on your own logged data\n"
        "  before quoting any of this as a behavioural finding.\n"
    )

    if not args.no_save:
        print(f"  Saved: {habit_model.MODEL_PATH}")
        print(f"  Card : {habit_model.METRICS_PATH}")

    # A quick sanity check that the model orders two obvious cases correctly.
    rough = habit_model.predict({
        "dow": 5, "days_since_last": 5, "streak": 0, "sessions_last_7": 0,
        "sleep_h": 4.5, "soreness": 5, "mood": 1, "planned_hour": 6,
        "completion_rate_30d": 0.2,
    })
    easy = habit_model.predict({
        "dow": 1, "days_since_last": 1, "streak": 12, "sessions_last_7": 5,
        "sleep_h": 8.5, "soreness": 1, "mood": 5, "planned_hour": 18,
        "completion_rate_30d": 0.9,
    })
    print(f"  Sanity check: worst-case user {rough.probability:.2f} ({rough.band}) "
          f"vs best-case user {easy.probability:.2f} ({easy.band})")
    if rough.probability <= easy.probability:
        print("  FAILED: the model does not order these correctly. Do not ship it.")
        return 1
    print("  OK - ordering is correct.\n")
    print(json.dumps({"top_drivers_worst_case": rough.drivers}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
