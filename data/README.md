# data/

Only the manifest and the test-split lock are committed. Rebuild the documents with:

```bash
python -m synth.build --per-type 200 --seed 42     # 600 synthetic docs -> data/datasets/synth/
python -m docex.ingest.cord --limit 300            # optional: CORD receipts from Hugging Face
```

* `manifest.json` — every document with its source, type, template, image-quality level, split and the
  SHA-256 of its input file and ground truth. `version` is a hash over all of them.
* `test_lock.json` — hash of the test-split document ids. `eval/run.py --split test` requires `--final`.
* Split: deterministic 60/20/20 from a hash of the document id (CORD keeps its official split).

Chromium renders fonts slightly differently across machines, so rebuilt images can differ by a few
pixels (ground truth does not). CI therefore evaluates the frozen subset in `eval/ci_data/`.
