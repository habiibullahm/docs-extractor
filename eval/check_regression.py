"""Fail CI when accuracy drops more than --max-drop (absolute) below the stored baseline.

    python -m eval.check_regression eval/reports/ci_A/metrics.json eval/baselines/ci_A.json
    python -m eval.check_regression <metrics> <baseline> --update     # accept new numbers
"""
import argparse
import json
import sys
from pathlib import Path

KEYS = ["field_accuracy", "doc_accuracy_critical", "critical_field_accuracy"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("metrics", type=Path)
    ap.add_argument("baseline", type=Path)
    ap.add_argument("--max-drop", type=float, default=0.02)
    ap.add_argument("--update", action="store_true")
    a = ap.parse_args()
    cur = json.loads(a.metrics.read_text())["overall"]
    snapshot = {k: round(cur[k], 4) for k in KEYS} | {"items_f1": round(cur["line_items"]["f1"], 4)}
    if a.update or not a.baseline.exists():
        a.baseline.parent.mkdir(parents=True, exist_ok=True)
        a.baseline.write_text(json.dumps(snapshot, indent=1))
        print(f"baseline written: {snapshot}")
        return
    base = json.loads(a.baseline.read_text())
    failed = False
    for k, v in snapshot.items():
        drop = base[k] - v
        status = "FAIL" if drop > a.max_drop else "ok"
        failed |= status == "FAIL"
        print(f"{status:4} {k:26} baseline {base[k]:.4f}  current {v:.4f}  delta {-drop:+.4f}")
    if failed:
        sys.exit(f"accuracy dropped more than {a.max_drop:.0%} below baseline")


if __name__ == "__main__":
    main()
