"""Loader for the public CORD v2 receipt dataset (naver-clova-ix/cord-v2, CC-BY-4.0).

    python -m docex.ingest.cord --limit 300

CORD receipts are Indonesian; their labels cover line items and totals but not
store name / date / payment method, so those fields are recorded as unlabeled in
``meta.json`` and skipped by the evaluator.
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
from typing import Any

from docex.config import DATA_DIR
from docex.normalize.core import parse_number

CORD_SPLIT_MAP = {"train": "train", "validation": "dev", "test": "test"}
LABELED_FIELDS = ["line_items", "subtotal", "discount", "tax", "service_charge", "total"]


def _as_list(x: Any) -> list:
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def cord_to_receipt(gt_parse: dict[str, Any]) -> dict[str, Any]:
    """Convert CORD ``gt_parse`` into our Receipt ground-truth (plain values)."""
    items = []
    for m in _as_list(gt_parse.get("menu")):
        name = m.get("nm")
        if isinstance(name, list):
            name = " ".join(str(n) for n in name)
        qty = parse_number(m.get("cnt")) if isinstance(m.get("cnt"), str) else None
        total = parse_number(m.get("price"))
        unit = parse_number(m.get("unitprice"))
        disc = parse_number(m.get("discountprice"))
        items.append({"description": name, "quantity": qty, "unit_price": unit, "discount": abs(disc) if disc else None,
                      "total": total, "is_adjustment": False})
    sub = gt_parse.get("sub_total") or {}
    tot = gt_parse.get("total") or {}
    if isinstance(sub, list):
        sub = sub[0] if sub else {}
    if isinstance(tot, list):
        tot = tot[0] if tot else {}
    disc = parse_number(sub.get("discount_price"))
    return {
        "doc_type": "receipt",
        "store_name": None, "store_address": None, "date": None, "time": None,
        "line_items": items,
        "subtotal": parse_number(sub.get("subtotal_price")),
        "discount": abs(disc) if disc else None,
        "tax": parse_number(sub.get("tax_price")),
        "service_charge": parse_number(sub.get("service_price")),
        "total": parse_number(tot.get("total_price")),
        "payment_method": None,
        "document_inconsistent": False,
    }


def download(limit: int | None, out_root: Path) -> int:
    from datasets import load_dataset  # optional dependency, only needed here

    n = 0
    for hf_split, our_split in CORD_SPLIT_MAP.items():
        ds = load_dataset("naver-clova-ix/cord-v2", split=hf_split)
        for i, row in enumerate(ds):
            if limit and n >= limit:
                return n
            gt_parse = json.loads(row["ground_truth"])["gt_parse"]
            doc_id = f"cord-{hf_split[:3]}-{i:04d}"
            d = out_root / doc_id
            d.mkdir(parents=True, exist_ok=True)
            img = row["image"]
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=92)
            (d / "input.jpg").write_bytes(buf.getvalue())
            (d / "gt.json").write_text(json.dumps(cord_to_receipt(gt_parse), ensure_ascii=False, indent=1))
            meta = {"doc_id": doc_id, "doc_type": "receipt", "source": "cord", "template": None,
                    "dirt": "real_photo", "input": "input.jpg", "split": our_split,
                    "labeled_fields": LABELED_FIELDS}
            (d / "meta.json").write_text(json.dumps(meta, indent=1))
            n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", type=Path, default=DATA_DIR / "datasets" / "cord")
    args = ap.parse_args()
    n = download(args.limit, args.out)
    from docex.ingest.dataset import build_manifest

    build_manifest()
    print(f"downloaded {n} CORD receipts to {args.out}")


if __name__ == "__main__":
    main()
