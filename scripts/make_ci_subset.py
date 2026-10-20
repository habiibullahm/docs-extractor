"""Freeze a small, stratified dev subset into eval/ci_data/ for the CI regression eval.

Rendering differs slightly across machines (fonts), so CI evaluates committed images
instead of regenerating them.

    python scripts/make_ci_subset.py --n 36
"""
import argparse
import shutil
from pathlib import Path

from docex.config import ROOT
from docex.ingest.dataset import build_manifest
from eval.run import select_docs

OUT = ROOT / "eval" / "ci_data"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=36)
    args = ap.parse_args()
    if OUT.exists():
        shutil.rmtree(OUT)
    for rec in select_docs("dev", args.n, None, ["synthetic"], seed=7):
        dst = OUT / "datasets" / "synth" / rec.doc_id
        dst.mkdir(parents=True)
        for name in ("gt.json", "meta.json", rec.input_path.name):
            shutil.copy(rec.dir / name, dst / name)
    m = build_manifest(OUT)
    print(f"{len(m['documents'])} docs -> {OUT}")


if __name__ == "__main__":
    main()
