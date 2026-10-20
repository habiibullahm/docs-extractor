"""Export human reviews as new labeled documents (data/datasets/corrections/), so
corrections feed back into evaluation and fine-tuning data.

    python -m docex.db.export_corrections
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from sqlalchemy import select

from docex.config import DATA_DIR
from docex.db.models import Document, session


def export(out_root: Path = DATA_DIR / "datasets" / "corrections") -> int:
    n = 0
    with session() as s:
        for doc in s.scalars(select(Document)).all():
            if not doc.reviews or not doc.latest:
                continue
            rv = doc.reviews[-1]
            if rv.action == "reject":
                continue
            values = rv.corrected_values if rv.action == "correct" else doc.latest.result.get("values")
            if not values:
                continue
            d = out_root / f"rev-{doc.id[:12]}"
            d.mkdir(parents=True, exist_ok=True)
            ext = Path(doc.storage_path).suffix
            shutil.copy(doc.storage_path, d / f"input{ext}")
            gt = {"doc_type": doc.latest.doc_type, **values}
            (d / "gt.json").write_text(json.dumps(gt, ensure_ascii=False, indent=1))
            meta = {"doc_id": d.name, "doc_type": doc.latest.doc_type, "source": "human_review",
                    "template": None, "dirt": "real_upload", "input": f"input{ext}", "split": "train",
                    "review_action": rv.action, "changed_fields": rv.changed_fields}
            (d / "meta.json").write_text(json.dumps(meta, indent=1))
            n += 1
    return n


if __name__ == "__main__":
    print(f"exported {export()} reviewed documents")
