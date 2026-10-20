"""Dataset checks: size, ground-truth schema validity, internal consistency and the locked split."""
import json

import pytest

from docex.config import DATA_DIR
from docex.ingest.dataset import iter_docs, load_manifest, verify_test_lock
from docex.schemas.documents import from_plain
from docex.validate.rules import Validator

pytestmark = pytest.mark.skipif(not (DATA_DIR / "datasets" / "synth").exists(),
                                reason="dataset not built (python -m synth.build)")


def test_size_and_splits():
    docs = load_manifest()["documents"]
    synth = [d for d in docs if d["source"] == "synthetic"]
    assert len(synth) >= 500
    assert {d["split"] for d in docs} == {"train", "dev", "test"}
    verify_test_lock()


def test_ground_truth_validates_against_schema_and_rules():
    v = Validator()
    for rec in iter_docs():
        gt = rec.gt()
        from_plain(rec.doc_type, gt)  # raises on schema violations
        if rec.source == "synthetic" and not gt.get("document_inconsistent"):
            issues = [i for i in v.validate(rec.doc_type, gt) if i.severity == "error"]
            assert not issues, (rec.doc_id, [i.message for i in issues])
