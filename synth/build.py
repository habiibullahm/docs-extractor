"""Build the synthetic dataset.

    python -m synth.build --per-type 200 --seed 42

Writes data/datasets/synth/<doc_id>/{input.pdf|input.jpg, page.png, gt.json, meta.json}
and (re)builds data/manifest.json with hashes and the train/dev/test split.
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

import cv2
from faker import Faker

from docex.config import DATA_DIR
from synth.content import GENERATORS
from synth.degrade import LEVELS, degrade
from synth.render import TEMPLATES, PdfRenderer, choose_style, pdf_to_png, render_html

INCONSISTENT_RATE = 0.04


def build(per_type: int, seed: int, out_root: Path, types: list[str]) -> list[dict]:
    rng = random.Random(seed)
    fake = Faker("id_ID")
    Faker.seed(seed)
    out_root.mkdir(parents=True, exist_ok=True)
    renderer = PdfRenderer()
    records = []
    t0 = time.time()
    try:
        for doc_type in types:
            for i in range(per_type):
                doc_id = f"syn-{doc_type[:3]}-{i:04d}"
                inconsistent = rng.random() < INCONSISTENT_RATE
                doc = GENERATORS[doc_type](doc_id, rng, fake, inconsistent=inconsistent)
                template = TEMPLATES[doc_type][i % len(TEMPLATES[doc_type])]
                style = choose_style(doc_type, template, doc.gt.get("currency"), rng)
                extra = dict(doc.render)
                if template == "invoice_modern":
                    extra["color"] = rng.choice(["#2b5797", "#1e7145", "#b91d47", "#603cba", "#e3a21a"])
                html = render_html(template, doc.gt, extra, style)
                d = out_root / doc_id
                d.mkdir(exist_ok=True)
                pdf = d / "doc.pdf"
                renderer.to_pdf(html, pdf, receipt=doc_type == "receipt")
                png = d / "page.png"
                pdf_to_png(pdf, png, dpi=200)
                level = LEVELS[i % len(LEVELS)] if i % 8 else rng.choice(LEVELS)
                if level == "clean":
                    input_name = "doc.pdf"
                    ops = {"level": "clean"}
                else:
                    img = cv2.imread(str(png))
                    img, ops = degrade(img, level, rng)
                    input_name = "input.jpg"
                    cv2.imwrite(str(d / input_name), img)
                gt = dict(doc.gt)
                (d / "gt.json").write_text(json.dumps(gt, ensure_ascii=False, indent=1))
                meta = {"doc_id": doc_id, "doc_type": doc_type, "source": "synthetic", "template": template,
                        "style": style, "dirt": level, "degradation": ops, "input": input_name,
                        "inconsistent": inconsistent}
                (d / "meta.json").write_text(json.dumps(meta, indent=1))
                records.append(meta)
                if len(records) % 50 == 0:
                    print(f"  {len(records)} docs ({time.time() - t0:.0f}s)")
    finally:
        renderer.close()
    return records


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-type", type=int, default=200)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--types", default="faktur_pajak,invoice,receipt")
    ap.add_argument("--out", type=Path, default=DATA_DIR / "datasets" / "synth")
    args = ap.parse_args()
    recs = build(args.per_type, args.seed, args.out, args.types.split(","))
    print(f"built {len(recs)} documents in {args.out}")
    from docex.ingest.dataset import build_manifest

    m = build_manifest()
    print(f"manifest: {len(m['documents'])} documents -> {DATA_DIR / 'manifest.json'}")


if __name__ == "__main__":
    main()
