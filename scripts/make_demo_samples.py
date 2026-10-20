"""Copy a few synthetic train-split documents into demo_samples/ for the live demo."""
import json
import shutil
from pathlib import Path

from docex.config import ROOT
from docex.ingest.dataset import iter_docs

OUT = ROOT / "demo_samples"
LABELS = {"faktur_pajak": "Faktur pajak", "invoice": "Invoice", "receipt": "Struk"}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    picked, index = set(), []
    for rec in iter_docs(split="train"):
        key = (rec.doc_type, rec.dirt, rec.template)
        if rec.dirt not in ("clean", "medium", "heavy") or key in picked:
            continue
        if sum(1 for k in picked if k[0] == rec.doc_type) >= 4:
            continue
        picked.add(key)
        name = f"{rec.doc_type}-{rec.dirt}-{rec.doc_id[-4:]}"
        dst = OUT / f"{name}{rec.input_path.suffix}"
        shutil.copy(rec.input_path, dst)
        index.append({"name": name, "file": dst.name, "label": f"{LABELS[rec.doc_type]}, {rec.dirt} ({rec.template})",
                      "doc_id": rec.doc_id})
    (OUT / "index.json").write_text(json.dumps(index, indent=1))
    print(f"{len(index)} samples -> {OUT}")


if __name__ == "__main__":
    main()
