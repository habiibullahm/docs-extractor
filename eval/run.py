"""Run the pipeline over a dataset split and write per-document results + report.

    python -m eval.run --approach A --split dev
    python -m eval.run --approach C --split dev --limit 60
    python -m eval.run --approach A --split test --final     # locked test set

Outputs eval/reports/<run_id>/{results.jsonl, metrics.json, report.md, threshold_curve.png, errors.md}
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

os.environ.setdefault("OMP_THREAD_LIMIT", "1")

from docex.config import pipeline_config  # noqa: E402
from docex.ingest.dataset import DocRecord, iter_docs, load_manifest, verify_test_lock  # noqa: E402
from docex.providers.llm import llm_available  # noqa: E402

REPORTS = Path(__file__).parent / "reports"

_PIPE = None


def _pipeline(approach: str, overrides: dict[str, Any]):
    global _PIPE
    if _PIPE is None:
        from docex.observability import configure_logging
        import logging

        configure_logging("WARNING")
        logging.getLogger().setLevel(logging.WARNING)
        from docex.pipeline.run import Pipeline

        _PIPE = Pipeline(approach, pipeline_config(**overrides))
    return _PIPE


def _process(args: tuple[DocRecord, str, dict[str, Any], bool, float]) -> dict[str, Any]:
    rec, approach, overrides, oracle_type, threshold = args
    pipe = _pipeline(approach, overrides)
    res = pipe.run(rec.input_path.read_bytes(), doc_type_hint=rec.doc_type if oracle_type else None,
                   threshold=threshold)
    out = res.to_dict()
    out.update(doc_id=rec.doc_id, gt_doc_type=rec.doc_type, source=rec.source, dirt=rec.dirt, split=rec.split,
               template=rec.template, labeled_fields=rec.labeled_fields)
    return out


def select_docs(split: str, limit: Optional[int], types: Optional[list[str]], sources: Optional[list[str]],
                seed: int = 0) -> list[DocRecord]:
    docs = [d for d in iter_docs(split=split, doc_types=types, verify=True) if not sources or d.source in sources]
    if limit and limit < len(docs):
        # stratified subset: keep doc type x dirt mix
        rng = random.Random(seed)
        by: dict[tuple[str, str], list[DocRecord]] = {}
        for d in docs:
            by.setdefault((d.doc_type, d.dirt), []).append(d)
        for v in by.values():
            rng.shuffle(v)
        out: list[DocRecord] = []
        while len(out) < limit:
            for k in sorted(by):
                if by[k] and len(out) < limit:
                    out.append(by[k].pop())
        docs = sorted(out, key=lambda d: d.doc_id)
    return docs


def main(argv: Optional[list[str]] = None) -> Path:
    ap = argparse.ArgumentParser()
    ap.add_argument("--approach", choices=["A", "B", "C"], required=True)
    ap.add_argument("--split", default="dev", choices=["train", "dev", "test"])
    ap.add_argument("--final", action="store_true", help="required to evaluate on the locked test split")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--types")
    ap.add_argument("--sources")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--run-id")
    ap.add_argument("--oracle-type", action="store_true", help="skip classification (use true doc type)")
    ap.add_argument("--model", help="override llm/vlm model")
    ap.add_argument("--ocr", help="override OCR provider (tesseract|rapidocr)")
    ap.add_argument("--prompt", help="override extraction prompt name")
    ap.add_argument("--no-repair", action="store_true")
    ap.add_argument("--threshold", type=float)
    args = ap.parse_args(argv)

    if args.split == "test":
        if not args.final:
            sys.exit("refusing to evaluate the locked test split without --final (do not tune on test)")
        verify_test_lock()

    cfg = pipeline_config()
    overrides: dict[str, Any] = {}
    if args.model:
        overrides["llm"] = {**cfg["llm"], "model": args.model}
        overrides["vlm"] = {**cfg["vlm"], "model": args.model}
    if args.ocr:
        overrides["ocr"] = {**cfg["ocr"], "provider": args.ocr}
    if args.prompt:
        overrides["prompts"] = {**cfg["prompts"], "extract": args.prompt}
    if args.no_repair:
        overrides["repair"] = {**cfg["repair"], "enabled": False}
    if args.approach in ("B", "C"):
        section = cfg["llm" if args.approach == "B" else "vlm"]
        if not llm_available(section):
            sys.exit(f"approach {args.approach} needs credentials for provider '{section['provider']}' "
                     "(set ANTHROPIC_API_KEY)")

    docs = select_docs(args.split, args.limit, args.types.split(",") if args.types else None,
                       args.sources.split(",") if args.sources else None)
    run_id = args.run_id or f"{args.approach}_{args.split}_{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir = REPORTS / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    from docex.scoring.score import default_threshold

    threshold = args.threshold if args.threshold is not None else default_threshold(args.approach, cfg["scoring"])
    print(f"[eval] approach={args.approach} split={args.split} docs={len(docs)} -> {out_dir}")

    jobs = [(d, args.approach, overrides, args.oracle_type, threshold) for d in docs]
    pool_cls = ProcessPoolExecutor if args.approach == "A" else ThreadPoolExecutor
    t0 = time.time()
    results = []
    with pool_cls(max_workers=args.workers) as pool, open(out_dir / "results.jsonl", "w") as f:
        for i, r in enumerate(pool.map(_process, jobs), 1):
            results.append(r)
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            if i % 25 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)} ({time.time() - t0:.0f}s)")
    run_meta = {
        "run_id": run_id, "approach": args.approach, "split": args.split, "n_docs": len(docs),
        "dataset_version": load_manifest()["version"], "threshold": threshold, "overrides": overrides,
        "oracle_type": args.oracle_type, "wall_time_s": round(time.time() - t0, 1), "workers": args.workers,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (out_dir / "run.json").write_text(json.dumps(run_meta, indent=1))
    from eval.report import build_report

    build_report(out_dir)
    print(f"[eval] report: {out_dir / 'report.md'}")
    return out_dir


if __name__ == "__main__":
    main()
