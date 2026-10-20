"""Field-, line-item- and document-level metrics."""
from __future__ import annotations

import re
from typing import Any, Optional

from rapidfuzz import fuzz

from docex.config import validation_config

NUM_ABS_TOL = 1.0      # IDR rounding
NUM_REL_TOL = 0.001
TEXT_FUZZY = 90.0      # rapidfuzz ratio (0-100)

ID_FIELDS = {"npwp", "nik", "tax_invoice_number", "account_number"}
CODE_FIELDS = {"invoice_number", "currency", "time", "date", "due_date"}
NUMERIC_FIELDS = {"quantity", "unit_price", "discount", "total", "subtotal", "tax", "service_charge", "dpp", "ppn",
                  "ppnbm"}

FIELDS: dict[str, list[str]] = {
    "faktur_pajak": ["tax_invoice_number", "date", "seller.name", "seller.npwp", "seller.address", "buyer.name",
                     "buyer.npwp", "buyer.nik", "buyer.address", "total", "discount", "dpp", "ppn", "ppnbm"],
    "invoice": ["invoice_number", "date", "due_date", "vendor.name", "vendor.npwp", "vendor.address", "customer.name",
                "customer.address", "subtotal", "discount", "tax", "total", "currency", "bank_account.bank_name",
                "bank_account.account_number", "bank_account.account_name"],
    "receipt": ["store_name", "store_address", "date", "time", "subtotal", "discount", "tax", "service_charge",
                "total", "payment_method"],
}


def get(d: Optional[dict[str, Any]], path: str) -> Any:
    cur: Any = d
    for p in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _norm_text(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def field_kind(path: str) -> str:
    leaf = path.split(".")[-1]
    if leaf in ID_FIELDS:
        return "id"
    if leaf in NUMERIC_FIELDS:
        return "number"
    if leaf in CODE_FIELDS:
        return "code"
    return "text"


def values_match(path: str, pred: Any, gt: Any) -> bool:
    kind = field_kind(path)
    if gt is None or gt == "":
        return pred is None or pred == ""
    if pred is None or pred == "":
        # an explicit zero amount that is printed as "0" vs missing is still a miss
        return False
    if kind == "number":
        try:
            p, g = float(pred), float(gt)
        except (TypeError, ValueError):
            return False
        return abs(p - g) <= max(NUM_ABS_TOL, NUM_REL_TOL * abs(g))
    if kind == "id":
        return re.sub(r"\D", "", str(pred)) == re.sub(r"\D", "", str(gt))
    if kind == "code":
        return re.sub(r"\s+", "", str(pred)).upper() == re.sub(r"\s+", "", str(gt)).upper()
    return fuzz.ratio(_norm_text(pred), _norm_text(gt)) >= TEXT_FUZZY


def _treat_zero_as_none(path: str, v: Any) -> Any:
    # A printed "0" discount/tax and an absent one are equivalent for evaluation.
    if field_kind(path) == "number" and path.split(".")[-1] in {"discount", "tax", "service_charge", "ppnbm"}:
        if v in (0, 0.0):
            return None
    return v


def compare_fields(doc_type: str, pred: Optional[dict[str, Any]], gt: dict[str, Any],
                   labeled: Optional[list[str]] = None) -> dict[str, dict[str, Any]]:
    """Per-field outcome: {'correct': bool, 'status': tp|miss|wrong|hallucinated|tn, 'pred', 'gt'}."""
    out = {}
    for path in FIELDS[doc_type]:
        if labeled is not None and path.split(".")[0] not in labeled:
            continue
        g = _treat_zero_as_none(path, get(gt, path))
        p = _treat_zero_as_none(path, get(pred, path)) if pred else None
        ok = values_match(path, p, g)
        if g is None:
            status = "tn" if ok else "hallucinated"
        elif p is None:
            status = "miss"
        else:
            status = "tp" if ok else "wrong"
        out[path] = {"correct": ok, "status": status, "pred": p, "gt": g}
    return out


def _item_sim(p: dict[str, Any], g: dict[str, Any]) -> float:
    d = fuzz.ratio(_norm_text(p.get("description") or ""), _norm_text(g.get("description") or "")) / 100
    t = 1.0 if values_match("total", p.get("total"), g.get("total")) else 0.0
    return 0.5 * d + 0.5 * t


def item_correct(p: dict[str, Any], g: dict[str, Any]) -> bool:
    return (values_match("description", p.get("description"), g.get("description"))
            and values_match("total", p.get("total"), g.get("total")))


def match_line_items(pred: list[dict[str, Any]], gt: list[dict[str, Any]]) -> dict[str, Any]:
    """Greedy max-similarity matching, then P/R/F1 over items that are fully correct
    (description fuzzy match + total within tolerance)."""
    pairs = sorted(((_item_sim(p, g), i, j) for i, p in enumerate(pred) for j, g in enumerate(gt)), reverse=True)
    used_p, used_g, matches = set(), set(), []
    for s, i, j in pairs:
        if s <= 0 or i in used_p or j in used_g:
            continue
        used_p.add(i)
        used_g.add(j)
        matches.append((i, j))
    correct = sum(1 for i, j in matches if item_correct(pred[i], gt[j]))
    qty_ok = sum(1 for i, j in matches if values_match("quantity", pred[i].get("quantity"), gt[j].get("quantity")))
    precision = correct / len(pred) if pred else (1.0 if not gt else 0.0)
    recall = correct / len(gt) if gt else (1.0 if not pred else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"n_pred": len(pred), "n_gt": len(gt), "correct": correct, "precision": precision, "recall": recall,
            "f1": f1, "qty_correct": qty_ok, "matched": len(matches)}


def critical_paths(doc_type: str, labeled: Optional[list[str]] = None) -> list[str]:
    paths = validation_config()["critical_fields"][doc_type]
    if labeled is not None:
        paths = [p for p in paths if p.split(".")[0] in labeled]
    return paths


def evaluate_document(doc_type: str, pred: Optional[dict[str, Any]], gt: dict[str, Any], pred_doc_type: str,
                      labeled: Optional[list[str]] = None) -> dict[str, Any]:
    if pred_doc_type != doc_type:
        pred = None
    fields = compare_fields(doc_type, pred, gt, labeled)
    crit = critical_paths(doc_type, labeled)
    crit_fields = {p: (fields[p]["correct"] if p in fields else
                       values_match(p, get(pred, p) if pred else None, get(gt, p))) for p in crit}
    items = match_line_items((pred or {}).get("line_items") or [], gt.get("line_items") or []) \
        if labeled is None or "line_items" in labeled else None
    critical_ok = all(crit_fields.values()) and pred_doc_type == doc_type
    # "fully correct" additionally requires every line item to be right
    fully_ok = critical_ok and (items is None or items["f1"] == 1.0)
    return {"fields": fields, "critical": crit_fields, "critical_ok": critical_ok, "items": items,
            "fully_correct": fully_ok, "classified_ok": pred_doc_type == doc_type}
