"""Dataset manifest, hashing and the locked train/dev/test split."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

from docex.config import DATA_DIR

MANIFEST = DATA_DIR / "manifest.json"
TEST_LOCK = DATA_DIR / "test_lock.json"
SPLITS = ("train", "dev", "test")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def split_for(doc_id: str, fixed: Optional[str] = None) -> str:
    """Deterministic 60/20/20 split from the doc id (stable across rebuilds)."""
    if fixed in SPLITS:
        return fixed  # e.g. CORD's own official split
    bucket = int(hashlib.sha1(doc_id.encode()).hexdigest(), 16) % 100
    return "train" if bucket < 60 else "dev" if bucket < 80 else "test"


def build_manifest(data_dir: Path = DATA_DIR) -> dict[str, Any]:
    docs = []
    for meta_path in sorted((data_dir / "datasets").glob("*/*/meta.json")):
        meta = json.loads(meta_path.read_text())
        d = meta_path.parent
        docs.append({
            "doc_id": meta["doc_id"],
            "path": str(d.relative_to(data_dir)),
            "doc_type": meta["doc_type"],
            "source": meta["source"],
            "template": meta.get("template"),
            "dirt": meta.get("dirt", "unknown"),
            "input": meta["input"],
            "input_sha256": sha256_file(d / meta["input"]),
            "gt_sha256": sha256_file(d / "gt.json"),
            "split": split_for(meta["doc_id"], meta.get("split")),
            "labeled_fields": meta.get("labeled_fields"),
        })
    version = hashlib.sha256("".join(x["input_sha256"] + x["gt_sha256"] for x in docs).encode()).hexdigest()[:16]
    manifest = {"version": version, "documents": docs}
    (data_dir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    test_ids = sorted(x["doc_id"] for x in docs if x["split"] == "test")
    lock = {
        "n_test": len(test_ids),
        "test_ids_sha256": hashlib.sha256("\n".join(test_ids).encode()).hexdigest(),
        "note": "Test split is locked: never use it for prompt tuning. eval/run.py requires --final to score it.",
    }
    (data_dir / "test_lock.json").write_text(json.dumps(lock, indent=1))
    return manifest


@dataclass
class DocRecord:
    doc_id: str
    doc_type: str
    source: str
    dirt: str
    split: str
    template: Optional[str]
    dir: Path
    input_path: Path
    labeled_fields: Optional[list[str]] = None

    def gt(self) -> dict[str, Any]:
        return json.loads((self.dir / "gt.json").read_text())

    def preview_path(self) -> Path:
        p = self.dir / "page.png"
        return p if p.exists() else self.input_path


def load_manifest(data_dir: Path = DATA_DIR) -> dict[str, Any]:
    return json.loads((data_dir / "manifest.json").read_text())


def iter_docs(split: Optional[str] = None, doc_types: Optional[list[str]] = None,
              data_dir: Path = DATA_DIR, verify: bool = False) -> Iterator[DocRecord]:
    m = load_manifest(data_dir)
    for x in m["documents"]:
        if split and x["split"] != split:
            continue
        if doc_types and x["doc_type"] not in doc_types:
            continue
        d = data_dir / x["path"]
        rec = DocRecord(x["doc_id"], x["doc_type"], x["source"], x["dirt"], x["split"], x.get("template"),
                        d, d / x["input"], x.get("labeled_fields"))
        if verify and sha256_file(rec.input_path) != x["input_sha256"]:
            raise ValueError(f"hash mismatch for {rec.doc_id}: dataset changed since manifest was built")
        yield rec


def verify_test_lock(data_dir: Path = DATA_DIR) -> None:
    lock = json.loads((data_dir / "test_lock.json").read_text())
    ids = sorted(x["doc_id"] for x in load_manifest(data_dir)["documents"] if x["split"] == "test")
    if hashlib.sha256("\n".join(ids).encode()).hexdigest() != lock["test_ids_sha256"]:
        raise RuntimeError("test split changed since it was locked")
