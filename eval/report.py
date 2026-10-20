"""Turn results.jsonl into metrics.json, report.md, threshold curve and error analysis."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from docex.config import DATA_DIR, pipeline_config
from docex.ingest.dataset import load_manifest
from eval.metrics import evaluate_document

TARGET_AUTO_ERROR = 0.01  # max share of wrong documents among auto-approved ones


def load_results(run_dir: Path) -> list[dict[str, Any]]:
    with open(run_dir / "results.jsonl") as f:
        return [json.loads(line) for line in f if line.strip()]


def _gt_index() -> dict[str, Path]:
    return {d["doc_id"]: DATA_DIR / d["path"] for d in load_manifest()["documents"]}


def score_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    idx = _gt_index()
    rows = []
    for r in results:
        d = idx[r["doc_id"]]
        gt = json.loads((d / "gt.json").read_text())
        ev = evaluate_document(r["gt_doc_type"], r.get("values"), gt, r["doc_type"], r.get("labeled_fields"))
        inconsistent = bool(gt.get("document_inconsistent"))
        ocr_span = next((s for s in r.get("trace", []) if s["name"] == "ocr"), None)
        rows.append({**r, "eval": ev, "gt_inconsistent": inconsistent,
                     "ok_for_auto": ev["fully_correct"] and not inconsistent,
                     "ocr_conf": ocr_span.get("mean_conf") if ocr_span else None, "doc_dir": str(d)})
    return rows


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _summary(rows: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    n = len(rows)
    if not n:
        return {"n": 0}
    field_tot = Counter()
    for r in rows:
        for f in r["eval"]["fields"].values():
            field_tot[f["status"]] += 1
    gt_present = field_tot["tp"] + field_tot["miss"] + field_tot["wrong"]
    gt_absent = field_tot["tn"] + field_tot["hallucinated"]
    crit = [v for r in rows for v in r["eval"]["critical"].values()]
    items = [r["eval"]["items"] for r in rows if r["eval"]["items"]]
    n_pred = sum(i["n_pred"] for i in items)
    n_gt = sum(i["n_gt"] for i in items)
    n_ok = sum(i["correct"] for i in items)
    p = n_ok / n_pred if n_pred else 0.0
    rc = n_ok / n_gt if n_gt else 0.0
    auto = [r for r in rows if r["score"] >= threshold and not (r.get("values") or {}).get("document_inconsistent")
            and r["doc_type"] != "other"]
    costs = np.array([r["usage"]["cost_usd"] for r in rows])
    lat = np.array([r["latency_ms"] for r in rows])
    cfg = pipeline_config()
    return {
        "n": n,
        "classification_accuracy": sum(r["eval"]["classified_ok"] for r in rows) / n,
        "field_accuracy": field_tot["tp"] / gt_present if gt_present else 0.0,
        "field_miss_rate": field_tot["miss"] / gt_present if gt_present else 0.0,
        "field_wrong_rate": field_tot["wrong"] / gt_present if gt_present else 0.0,
        "hallucination_rate": field_tot["hallucinated"] / gt_absent if gt_absent else 0.0,
        "critical_field_accuracy": sum(crit) / len(crit) if crit else 0.0,
        "doc_accuracy_critical": sum(r["eval"]["critical_ok"] for r in rows) / n,
        "doc_fully_correct": sum(r["eval"]["fully_correct"] for r in rows) / n,
        "line_items": {"precision": p, "recall": rc, "f1": 2 * p * rc / (p + rc) if p + rc else 0.0,
                       "doc_mean_f1": float(np.mean([i["f1"] for i in items])) if items else 0.0},
        "auto_approve_rate": len(auto) / n,
        "straight_through_rate": sum(r["ok_for_auto"] for r in auto) / n,
        "auto_error_rate": (sum(not r["ok_for_auto"] for r in auto) / len(auto)) if auto else 0.0,
        "repair_rate": sum(bool(r.get("repaired")) for r in rows) / n,
        "pipeline_errors": sum(bool(r.get("error")) for r in rows),
        "cost_usd_per_doc": float(costs.mean()),
        "cost_usd_per_1000": float(costs.mean() * 1000),
        "cost_idr_per_1000": float(costs.mean() * 1000 * cfg["costs"]["usd_to_idr"]),
        "api_cost_usd_per_doc": float(np.mean([r["usage"]["api_cost_usd"] for r in rows])),
        "tokens_in_per_doc": float(np.mean([r["usage"]["input_tokens"] for r in rows])),
        "tokens_out_per_doc": float(np.mean([r["usage"]["output_tokens"] for r in rows])),
        "latency_p50_ms": float(np.percentile(lat, 50)),
        "latency_p95_ms": float(np.percentile(lat, 95)),
    }


def threshold_curve(rows: list[dict[str, Any]]) -> list[dict[str, float]]:
    n = len(rows)
    curve = []
    for t in np.round(np.arange(0.0, 1.0001, 0.01), 2):
        auto = [r for r in rows if r["score"] >= t and not (r.get("values") or {}).get("document_inconsistent")
                and r["doc_type"] != "other"]
        wrong = sum(not r["ok_for_auto"] for r in auto)
        curve.append({"threshold": float(t), "auto_rate": len(auto) / n,
                      "stp_rate": sum(r["ok_for_auto"] for r in auto) / n,
                      "auto_error_rate": wrong / len(auto) if auto else 0.0, "n_auto": len(auto)})
    return curve


def recommend_threshold(curve: list[dict[str, float]], target: float = TARGET_AUTO_ERROR) -> dict[str, Any]:
    ok = [c for c in curve if c["auto_error_rate"] <= target and c["n_auto"] > 0]
    if not ok:
        return {"threshold": None, "reason": f"no threshold reaches auto-error <= {pct(target)}; route all to review"}
    best = max(ok, key=lambda c: (c["stp_rate"], -c["threshold"]))
    return {**best, "target_auto_error": target,
            "reason": f"highest straight-through rate with auto-approve error <= {pct(target)}"}


def plot_curve(curve: list[dict[str, float]], out: Path, title: str, chosen: float | None) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    t = [c["threshold"] for c in curve]
    fig, ax = plt.subplots(figsize=(7, 4), dpi=110)
    ax.plot(t, [c["stp_rate"] for c in curve], label="straight-through (auto & correct)", color="#2a6fdb", lw=2)
    ax.plot(t, [c["auto_rate"] for c in curve], label="auto-approve rate", color="#8fb3ee", lw=1.5, ls="--")
    ax.plot(t, [c["auto_error_rate"] for c in curve], label="error rate among auto-approved", color="#d1495b", lw=2)
    if chosen is not None:
        ax.axvline(chosen, color="#444", ls=":", lw=1)
        ax.text(chosen + 0.01, 0.92, f"t={chosen:.2f}", fontsize=8)
    ax.set_xlabel("score threshold")
    ax.set_ylabel("share of documents")
    ax.set_ylim(0, 1.02)
    ax.set_title(title, fontsize=10)
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8, loc="center left")
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)


def _group(rows: Iterable[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    g: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        g[r[key]].append(r)
    return dict(sorted(g.items()))


def per_field(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    out: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    for r in rows:
        for path, f in r["eval"]["fields"].items():
            out[r["gt_doc_type"]][path][f["status"]] += 1
    res: dict[str, dict[str, dict[str, Any]]] = {}
    for dt, fields in out.items():
        res[dt] = {}
        for path, c in fields.items():
            present = c["tp"] + c["miss"] + c["wrong"]
            res[dt][path] = {"n_present": present, "accuracy": c["tp"] / present if present else None,
                             "miss": c["miss"], "wrong": c["wrong"], "hallucinated": c["hallucinated"]}
    return res


def _cause(r: dict[str, Any], threshold: float) -> str:
    ev = r["eval"]
    causes = []
    if r.get("error"):
        causes.append(f"pipeline error: {r['error'][:80]}")
    if not ev["classified_ok"]:
        causes.append(f"misclassified as `{r['doc_type']}`")
    if r.get("ocr_conf") is not None and r["ocr_conf"] < 0.75:
        causes.append(f"poor OCR (mean conf {r['ocr_conf']:.2f}, dirt={r['dirt']})")
    st = Counter(f["status"] for f in ev["fields"].values())
    if st["miss"]:
        causes.append(f"{st['miss']} field(s) not found")
    if st["wrong"]:
        causes.append(f"{st['wrong']} field(s) misread")
    if st["hallucinated"]:
        causes.append(f"{st['hallucinated']} hallucinated field(s)")
    if ev["items"] and ev["items"]["f1"] < 1:
        causes.append(f"line items F1 {ev['items']['f1']:.2f} ({ev['items']['n_pred']} pred / {ev['items']['n_gt']} gt)")
    if r["score"] >= threshold and not r["ok_for_auto"]:
        causes.insert(0, "**SILENT ERROR: auto-approved but wrong**")
    if r.get("gt_inconsistent"):
        causes.append("document is deliberately inconsistent")
    return "; ".join(causes) or "-"


def error_analysis(rows: list[dict[str, Any]], out_dir: Path, threshold: float, k: int = 20) -> str:
    from PIL import Image

    def badness(r: dict[str, Any]) -> float:
        ev = r["eval"]
        wrong_crit = sum(not v for v in ev["critical"].values())
        wrong_fields = sum(not f["correct"] for f in ev["fields"].values())
        f1 = ev["items"]["f1"] if ev["items"] else 1.0
        silent = 5 if (r["score"] >= threshold and not r["ok_for_auto"]) else 0
        return silent + 2 * wrong_crit + wrong_fields + 3 * (1 - f1) + (3 if not ev["classified_ok"] else 0)

    worst = sorted([r for r in rows if badness(r) > 0], key=badness, reverse=True)[:k]
    img_dir = out_dir / "errors"
    img_dir.mkdir(exist_ok=True)
    lines = [f"# Error analysis — {len(worst)} worst documents", "",
             "Ranked by: silent auto-approve errors, wrong critical fields, wrong fields, line-item F1.", ""]
    for rank, r in enumerate(worst, 1):
        d = Path(r["doc_dir"])
        src = next((p for p in (d / "input.jpg", d / "page.png") if p.exists()), None)
        img_md = ""
        if src:
            im = Image.open(src).convert("RGB")
            im.thumbnail((600, 800))
            im.save(img_dir / f"{r['doc_id']}.jpg", quality=80)
            img_md = f"<img src=\"errors/{r['doc_id']}.jpg\" width=\"300\">"
        lines += [f"## {rank}. `{r['doc_id']}` — {r['gt_doc_type']}, dirt={r['dirt']}, template={r['template']}",
                  "", f"score={r['score']:.2f}, route={r['route']} | cause: {_cause(r, threshold)}", "", img_md, "",
                  "| field | predicted | ground truth | status |", "|---|---|---|---|"]
        for path, f in r["eval"]["fields"].items():
            if not f["correct"]:
                lines.append(f"| {path} | {str(f['pred'])[:60]} | {str(f['gt'])[:60]} | {f['status']} |")
        if r["issues"]:
            lines.append("")
            lines.append("Validation: " + "; ".join(i["message"] for i in r["issues"][:4]))
        lines.append("")
    text = "\n".join(lines)
    (out_dir / "errors.md").write_text(text)
    return text


def build_report(run_dir: Path) -> dict[str, Any]:
    run = json.loads((run_dir / "run.json").read_text())
    threshold = run["threshold"]
    rows = score_results(load_results(run_dir))
    curve = threshold_curve(rows)
    rec = recommend_threshold(curve)
    metrics = {
        "run": run,
        "overall": _summary(rows, threshold),
        "by_doc_type": {k: _summary(v, threshold) for k, v in _group(rows, "gt_doc_type").items()},
        "by_dirt": {k: _summary(v, threshold) for k, v in _group(rows, "dirt").items()},
        "by_source": {k: _summary(v, threshold) for k, v in _group(rows, "source").items()},
        "per_field": per_field(rows),
        "threshold_curve": curve,
        "recommended_threshold": rec,
    }
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=1))
    plot_curve(curve, run_dir / "threshold_curve.png", f"Approach {run['approach']} — {run['split']} (n={len(rows)})",
               rec.get("threshold"))
    error_analysis(rows, run_dir, threshold)
    (run_dir / "report.md").write_text(render_markdown(metrics))
    return metrics


def _row(name: str, s: dict[str, Any]) -> str:
    if not s.get("n"):
        return f"| {name} | 0 | | | | | | | |"
    return (f"| {name} | {s['n']} | {pct(s['classification_accuracy'])} | {pct(s['field_accuracy'])} | "
            f"{pct(s['doc_accuracy_critical'])} | {s['line_items']['f1']:.3f} | {pct(s['straight_through_rate'])} | "
            f"{pct(s['auto_error_rate'])} | {s['latency_p50_ms'] / 1000:.1f}s / {s['latency_p95_ms'] / 1000:.1f}s |")


HEADER = ("| group | n | classif. acc | field acc | doc acc (critical) | items F1 | STP | auto-approve error | "
          "latency p50/p95 |\n|---|---|---|---|---|---|---|---|---|")


def render_markdown(m: dict[str, Any]) -> str:
    run, o = m["run"], m["overall"]
    lines = [
        f"# Eval report — approach {run['approach']} on `{run['split']}`", "",
        f"Run `{run['run_id']}` · dataset `{run['dataset_version']}` · {run['n_docs']} documents · "
        f"threshold {run['threshold']:.2f} · generated by `eval/run.py` (do not edit by hand)", "",
        "## Summary", "", HEADER, _row("**all**", o), "",
        f"- Hallucination rate (value returned where ground truth is empty): {pct(o['hallucination_rate'])}",
        f"- Field miss rate {pct(o['field_miss_rate'])}, wrong-value rate {pct(o['field_wrong_rate'])}",
        f"- Line items: precision {o['line_items']['precision']:.3f}, recall {o['line_items']['recall']:.3f}",
        f"- Documents fully correct (critical fields + all line items): {pct(o['doc_fully_correct'])}",
        f"- Auto-approve rate {pct(o['auto_approve_rate'])}; repair pass used on {pct(o['repair_rate'])} of documents; "
        f"pipeline errors: {o['pipeline_errors']}",
        f"- Cost: ${o['cost_usd_per_doc']:.5f}/doc (API ${o['api_cost_usd_per_doc']:.5f}) → "
        f"**${o['cost_usd_per_1000']:.2f} / Rp{o['cost_idr_per_1000']:,.0f} per 1,000 documents**; "
        f"tokens in/out per doc {o['tokens_in_per_doc']:.0f}/{o['tokens_out_per_doc']:.0f}",
        "", "## By document type", "", HEADER,
        *[_row(k, v) for k, v in m["by_doc_type"].items()],
        "", "## By image quality (dirt level)", "", HEADER,
        *[_row(k, v) for k, v in m["by_dirt"].items()],
        "", "## Threshold selection", "",
        "![threshold curve](threshold_curve.png)", "",
        "| threshold | auto-approve | straight-through | error among auto |", "|---|---|---|---|",
    ]
    for c in m["threshold_curve"]:
        if round(c["threshold"] * 100) % 10 == 0 or c["threshold"] == (m["recommended_threshold"].get("threshold")):
            lines.append(f"| {c['threshold']:.2f} | {pct(c['auto_rate'])} | {pct(c['stp_rate'])} | "
                         f"{pct(c['auto_error_rate'])} |")
    r = m["recommended_threshold"]
    lines += ["", f"**Recommended threshold:** {r.get('threshold')} — {r['reason']}" +
              (f" (STP {pct(r['stp_rate'])}, auto-error {pct(r['auto_error_rate'])})" if r.get("threshold") is not None
               else ""), "", "## Per-field accuracy", ""]
    for dt, fields in m["per_field"].items():
        lines += [f"### {dt}", "", "| field | n (gt present) | accuracy | missed | wrong | hallucinated |",
                  "|---|---|---|---|---|---|"]
        for path, f in fields.items():
            acc = pct(f["accuracy"]) if f["accuracy"] is not None else "–"
            lines.append(f"| {path} | {f['n_present']} | {acc} | {f['miss']} | {f['wrong']} | {f['hallucinated']} |")
        lines.append("")
    lines += ["See [errors.md](errors.md) for the 20 worst documents with images and causes."]
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    build_report(Path(sys.argv[1]))
