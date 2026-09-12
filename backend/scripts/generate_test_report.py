"""
Generate docs/TESTING_REPORT.md from a real test run.

    cd backend
    .venv\\Scripts\\python.exe scripts/generate_test_report.py

Every number in the report is produced by actually running the suite and the
coverage tool at the moment you run this. Nothing is typed in by hand, which
is the only way a testing report stays true after the code changes.

The report also folds in the fusion evaluation results from eval_out/ if they
are present, so the submission has functional testing and experimental
validation in one document.
"""
from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent
OUT = ROOT / "docs" / "TESTING_REPORT.md"
PY = sys.executable


def run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=BACKEND, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def parse_tests(output: str) -> list[dict[str, str]]:
    """Pull one row per test out of pytest -v output."""
    rows = []
    for line in output.splitlines():
        m = re.match(r"^(tests[/\\][\w_]+\.py)::(\w+)\s+(PASSED|FAILED|ERROR|SKIPPED)", line)
        if m:
            rows.append({"file": m.group(1), "test": m.group(2), "status": m.group(3)})
    return rows


def parse_summary(output: str) -> dict[str, int]:
    m = re.search(r"(\d+) passed", output)
    passed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) failed", output)
    failed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) error", output)
    errors = int(m.group(1)) if m else 0
    m = re.search(r"in ([\d.]+)s", output)
    seconds = float(m.group(1)) if m else 0.0
    return {"passed": passed, "failed": failed, "errors": errors, "seconds": seconds}


def parse_coverage() -> tuple[list[dict], float]:
    """Read coverage.json produced by pytest-cov."""
    path = BACKEND / "coverage.json"
    if not path.exists():
        return [], 0.0
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for fname, info in sorted(data.get("files", {}).items()):
        s = info["summary"]
        if s["num_statements"] == 0:
            continue
        rows.append({
            "file": fname.replace("\\", "/"),
            "statements": s["num_statements"],
            "missing": s["missing_lines"],
            "percent": round(s["percent_covered"], 1),
        })
    total = round(data.get("totals", {}).get("percent_covered", 0.0), 1)
    return rows, total


def eval_table() -> str:
    path = BACKEND / "eval_out" / "summary.csv"
    if not path.exists():
        return (
            "_Not available. Run `scripts/run_evaluation.py` to generate "
            "`eval_out/summary.csv`, then re-run this script._\n"
        )
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    lines = [
        "| Condition | Modality | Precision | Recall | F1 | MAE (reps) |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for r in rows:
        bold = "**" if r["modality"] == "fused" else ""
        lines.append(
            f"| {r['condition']} | {bold}{r['modality']}{bold} | "
            f"{float(r['precision']):.3f} | {float(r['recall']):.3f} | "
            f"{bold}{float(r['f1']):.3f}{bold} | {float(r['mae_count']):.2f} |"
        )
    return "\n".join(lines)


def main() -> int:
    print("Running the test suite with coverage (this takes ~30 s)...")
    code, output = run([
        PY, "-m", "pytest", "tests/", "-v",
        "--cov=app", "--cov-report=json", "--cov-report=term",
    ])
    tests = parse_tests(output)
    summary = parse_summary(output)
    cov_rows, cov_total = parse_coverage()

    if not tests:
        print("Could not parse any test results. Raw output:\n")
        print(output[-3000:])
        return 1

    status = "PASS" if summary["failed"] == 0 and summary["errors"] == 0 else "FAIL"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # group tests by the section comment they sit under
    groups: dict[str, list[dict]] = {}
    for t in tests:
        name = t["test"]
        if name.startswith("test_admin"):
            g = "Admin dashboard & access control"
        elif "websocket" in name or "fusion" in name or "clock_offset" in name:
            g = "Sensor fusion & live WebSocket"
        elif "diet" in name or "mifflin" in name:
            g = "Diet engine"
        elif "habit" in name:
            g = "Habit model"
        elif "chat" in name:
            g = "Coaching chat"
        elif "imu" in name:
            g = "IMU ingest"
        elif "login" in name or "auth" in name or "registration" in name or "token" in name:
            g = "Authentication"
        else:
            g = "Service health & metadata"
        groups.setdefault(g, []).append(t)

    parts: list[str] = [
        "# Testing Report",
        "",
        f"**Generated:** {now}  ",
        f"**Result:** {status} — {summary['passed']} passed, "
        f"{summary['failed']} failed, {summary['errors']} errors "
        f"in {summary['seconds']:.1f}s  ",
        f"**Backend coverage:** {cov_total}% of statements",
        "",
        "Produced by `backend/scripts/generate_test_report.py`, which runs the "
        "suite and the coverage tool and writes this file from their actual "
        "output. No figure in this document is typed in by hand.",
        "",
        "---",
        "",
        "## 1. Test strategy",
        "",
        "The tests are deliberately **integration-level rather than unit-level**. "
        "In a system of this shape the defects live in the wiring between "
        "layers — an auth dependency not applied, a schema field not "
        "serialised, a WebSocket message arriving out of order — not in the "
        "arithmetic inside a single function. Each test therefore drives a "
        "real HTTP or WebSocket request through FastAPI, SQLAlchemy and a "
        "temporary database.",
        "",
        "Three tests assert the project's central claim directly, so the "
        "suite fails if the contribution regresses:",
        "",
        "- `test_fusion_beats_camera_under_occlusion`",
        "- `test_clock_offset_is_recovered`",
        "- `test_fusion_rejects_imu_bumps_the_camera_never_saw`",
        "",
        "The last of these sweeps eight random seeds and additionally asserts "
        "that at least one seed genuinely produced the IMU over-count it is "
        "testing against — otherwise a detector that silently stopped "
        "detecting anything would pass a vacuous test.",
        "",
        "## 2. Results by area",
        "",
    ]

    for g, items in groups.items():
        ok = sum(1 for i in items if i["status"] == "PASSED")
        parts.append(f"### {g} — {ok}/{len(items)} passed")
        parts.append("")
        parts.append("| Test | Status |")
        parts.append("|---|---|")
        for i in items:
            mark = "PASS" if i["status"] == "PASSED" else i["status"]
            parts.append(f"| `{i['test']}` | {mark} |")
        parts.append("")

    parts += [
        "## 3. Coverage by module",
        "",
        "| Module | Statements | Missing | Coverage |",
        "|---|---:|---:|---:|",
    ]
    for r in cov_rows:
        parts.append(
            f"| `{r['file']}` | {r['statements']} | {r['missing']} | {r['percent']}% |"
        )
    parts += [
        f"| **TOTAL** | | | **{cov_total}%** |",
        "",
        "Uncovered lines are concentrated in the optional subsystems — the "
        "MQTT subscriber and the MongoDB archive — because the test "
        "environment deliberately runs with both disabled, to prove the API "
        "works without them. The LLM branch of `chat_service` is likewise "
        "uncovered by design: the tests assert the deterministic fallback, "
        "so the suite never makes a paid network call.",
        "",
        "## 4. Experimental validation (sensor fusion)",
        "",
        "Functional tests prove the system runs. The table below measures "
        "whether the contribution actually works, over six controlled "
        "conditions with known ground truth. Generated by "
        "`scripts/run_evaluation.py`.",
        "",
        eval_table(),
        "",
        "## 5. Known gaps",
        "",
        "Stated plainly, because an examiner will find them anyway:",
        "",
        "1. **The evaluation above uses simulated sensor data.** It establishes "
        "why and when fusion helps under conditions that cannot be produced "
        "repeatably with a human. It is not a validation on human subjects.",
        "2. **The habit model is trained on a synthetic population.** Its AUC "
        "validates the pipeline, not human behaviour.",
        "3. **Form scoring is unvalidated against a clinician.** The rules are "
        "geometric heuristics with plausible thresholds.",
        "4. **No load or concurrency testing.** The system has been exercised "
        "with one user at a time.",
        "5. **No browser-automation tests.** The React layer is verified "
        "manually and by a production build; the API beneath it is covered "
        "by the suite above.",
        "",
    ]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(parts), encoding="utf-8")

    # coverage.json is a build artefact, not a deliverable
    (BACKEND / "coverage.json").unlink(missing_ok=True)
    (BACKEND / ".coverage").unlink(missing_ok=True)

    print(f"\n{status}: {summary['passed']} passed in {summary['seconds']:.1f}s, "
          f"coverage {cov_total}%")
    print(f"Wrote {OUT}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
