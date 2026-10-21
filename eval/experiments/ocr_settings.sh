#!/bin/sh
# Experiment: Tesseract page segmentation mode and OCR engine for approach A (train sample only).
# Reports land in eval/reports/exp_*; docs/technical.md, "Technical decisions" cites them.
set -e
for psm in 3 4 6 11; do
  DOCEX_OCR__PSM=$psm python -m eval.run --approach A --split train --limit 60 --run-id exp_psm${psm}_train60
done
python -m eval.run --approach A --split train --limit 60 --ocr rapidocr --run-id exp_rapidocr_train60
