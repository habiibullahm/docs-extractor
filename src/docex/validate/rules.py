"""Configurable rule-based validation of extracted documents."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any, Optional

from docex.config import tax_rules, validation_config


@dataclass
class Issue:
    rule: str
    severity: str  # "error" | "warning"
    message: str
    fields: list[str] = field(default_factory=list)
    # Machine-readable version of the arithmetic behind the message, for the review UI:
    # {"field", "expected", "actual", "difference", "tolerance", "terms": [{"op", "field"|"label", "value"}]}.
    # None for rules that are not a calculation. The message text is unchanged (the repair prompt uses it).
    expected: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if d["expected"] is None:
            del d["expected"]
        return d


def _term(value: float, op: Optional[str] = None, *, field: Optional[str] = None, label: Optional[str] = None) -> dict[str, Any]:
    t: dict[str, Any] = {"value": value}
    if op:
        t["op"] = op
    if field:
        t["field"] = field
    if label:
        t["label"] = label
    return t


def _expected(field: str, expected: float, actual: float, tolerance: float, terms: list[dict[str, Any]]) -> dict[str, Any]:
    return {"field": field, "expected": round(expected, 2), "actual": round(actual, 2),
            "difference": round(abs(actual - expected), 2), "tolerance": round(tolerance, 2), "terms": terms}


def get_path(doc: dict[str, Any], path: str) -> Any:
    cur: Any = doc
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _close(a: float, b: float, abs_tol: float, rel_tol: float) -> bool:
    return abs(a - b) <= max(abs_tol, rel_tol * max(abs(a), abs(b)))


class Validator:
    def __init__(self, cfg: Optional[dict[str, Any]] = None, rules: Optional[dict[str, Any]] = None,
                 today: Optional[date] = None) -> None:
        self.cfg = cfg or validation_config()
        self.tax = rules or tax_rules()
        self.today = today or date.today()

    def _rule(self, name: str) -> Optional[dict[str, Any]]:
        r = self.cfg["rules"].get(name, {})
        return r if r.get("enabled", True) else None

    def validate(self, doc_type: str, values: dict[str, Any]) -> list[Issue]:
        """``values`` is the plain (confidence-free) normalized document."""
        issues: list[Issue] = []
        add = issues.append
        sev = lambda name: self.cfg["rules"].get(name, {}).get("severity", "error")  # noqa: E731

        if r := self._rule("required_fields"):
            for path in self.cfg["critical_fields"].get(doc_type, []):
                if get_path(values, path) in (None, ""):
                    add(Issue("required_fields", r["severity"], f"critical field '{path}' is missing", [path]))

        items = values.get("line_items") or []
        item_totals = [it.get("total") for it in items]
        sum_target = {"faktur_pajak": "total", "invoice": "subtotal", "receipt": "subtotal"}[doc_type]
        target = values.get(sum_target)
        derived = False
        if doc_type == "receipt" and target is None:
            derived = True
            target, sum_target = values.get("total"), "total"
            extras = sum(values.get(k) or 0 for k in ("service_charge", "tax")) - (values.get("discount") or 0)
            target = None if target is None else target - extras
        if (r := self._rule("line_items_sum")) and items and target is not None:
            if any(t is None for t in item_totals):
                add(Issue("line_items_sum", "warning", "some line items have no total", ["line_items"]))
            else:
                s = sum(item_totals)
                if not _close(s, target, r["abs_tolerance"], r["rel_tolerance"]):
                    add(Issue("line_items_sum", r["severity"],
                              f"sum of line items ({s:,.2f}) != {sum_target} ({target:,.2f})",
                              ["line_items", sum_target],
                              None if derived else _expected(
                                  sum_target, s, target, max(r["abs_tolerance"], r["rel_tolerance"] * max(abs(s), abs(target))),
                                  [])))

        if r := self._rule("totals_arithmetic"):
            self._arithmetic(doc_type, values, r, add)

        if (r := self._rule("ppn_rate")) and doc_type in ("faktur_pajak", "invoice"):
            self._ppn(doc_type, values, r, add)

        if r := self._rule("npwp_format"):
            ok_len = {self.tax["npwp"]["legacy_length"], self.tax["npwp"]["new_length"]}
            for path in ("seller.npwp", "buyer.npwp", "vendor.npwp", "customer.npwp"):
                v = get_path(values, path)
                if v and len(v) not in ok_len:
                    add(Issue("npwp_format", r["severity"], f"{path} has {len(v)} digits (expected 15 or 16)", [path]))
        if r := self._rule("nik_format"):
            for path in ("buyer.nik", "seller.nik"):
                v = get_path(values, path)
                if v and len(v) != self.tax["nik"]["length"]:
                    add(Issue("nik_format", r["severity"], f"{path} has {len(v)} digits (expected 16)", [path]))
        if (r := self._rule("faktur_serial_format")) and doc_type == "faktur_pajak":
            v = values.get("tax_invoice_number")
            allowed = {self.tax["faktur_serial"]["legacy"]["digits"], self.tax["faktur_serial"]["coretax"]["digits"]}
            if v and len(v) not in allowed:
                add(Issue("faktur_serial_format", r["severity"],
                          f"faktur serial has {len(v)} digits (expected {sorted(allowed)})", ["tax_invoice_number"]))

        if r := self._rule("date_plausible"):
            for path in ("date",):
                v = values.get(path)
                if not v:
                    continue
                try:
                    d = date.fromisoformat(v)
                except ValueError:
                    add(Issue("date_plausible", r["severity"], f"{path} is not a valid ISO date", [path]))
                    continue
                if d > self.today + timedelta(days=r["allow_future_days"]):
                    add(Issue("date_plausible", r["severity"], f"{path} {v} is in the future", [path]))
                elif d < self.today - timedelta(days=365 * r["max_age_years"]):
                    add(Issue("date_plausible", r["severity"], f"{path} {v} is older than {r['max_age_years']} years", [path]))
        if (r := self._rule("due_after_issue")) and doc_type == "invoice":
            d1, d2 = values.get("date"), values.get("due_date")
            if d1 and d2 and d2 < d1:
                add(Issue("due_after_issue", r["severity"], "due date is before invoice date", ["date", "due_date"]))
        return issues

    def _arithmetic(self, doc_type: str, v: dict[str, Any], r: dict[str, Any], add) -> None:
        tol = (r["abs_tolerance"], r["rel_tolerance"])
        if doc_type == "invoice":
            sub, tot = v.get("subtotal"), v.get("total")
            if sub is not None and tot is not None:
                exp = sub - (v.get("discount") or 0) + (v.get("tax") or 0)
                if not _close(exp, tot, *tol):
                    add(Issue("totals_arithmetic", r["severity"],
                              f"subtotal - discount + tax = {exp:,.2f} but total = {tot:,.2f}",
                              ["subtotal", "discount", "tax", "total"],
                              _expected("total", exp, tot, max(tol[0], tol[1] * max(abs(exp), abs(tot))), [
                                  _term(sub, field="subtotal"), _term(v.get("discount") or 0, "-", field="discount"),
                                  _term(v.get("tax") or 0, "+", field="tax")])))
        elif doc_type == "receipt":
            sub, tot = v.get("subtotal"), v.get("total")
            if sub is not None and tot is not None:
                exp = sub - (v.get("discount") or 0) + (v.get("tax") or 0) + (v.get("service_charge") or 0)
                if not _close(exp, tot, *tol):
                    add(Issue("totals_arithmetic", r["severity"],
                              f"subtotal - discount + service + tax = {exp:,.2f} but total = {tot:,.2f}",
                              ["subtotal", "discount", "tax", "service_charge", "total"],
                              _expected("total", exp, tot, max(tol[0], tol[1] * max(abs(exp), abs(tot))), [
                                  _term(sub, field="subtotal"), _term(v.get("discount") or 0, "-", field="discount"),
                                  _term(v.get("service_charge") or 0, "+", field="service_charge"),
                                  _term(v.get("tax") or 0, "+", field="tax")])))
        elif doc_type == "faktur_pajak":
            tot, dpp = v.get("total"), v.get("dpp")
            if tot is not None and dpp is not None:
                base = tot - (v.get("discount") or 0)
                factors = {s["dpp_factor"] for s in self.tax["ppn"]["schemes"].values()}
                if not any(_close(base * f, dpp, max(tol[0], 1.0), tol[1]) for f in factors):
                    f = min(factors, key=lambda x: abs(base * x - dpp))
                    add(Issue("totals_arithmetic", r["severity"],
                              f"DPP {dpp:,.2f} does not match (total - discount) = {base:,.2f} under any DPP scheme",
                              ["total", "discount", "dpp"],
                              _expected("dpp", base * f, dpp, max(tol[0], 1.0, tol[1] * dpp), [
                                  _term(tot, field="total"), _term(v.get("discount") or 0, "-", field="discount"),
                                  _term(f, "x", label="dpp_factor")])))

    def _ppn(self, doc_type: str, v: dict[str, Any], r: dict[str, Any], add) -> None:
        schemes = self.tax["ppn"]["schemes"]
        tol = self.tax["ppn"]["rounding_tolerance"]
        if doc_type == "faktur_pajak":
            dpp, ppn = v.get("dpp"), v.get("ppn")
            if dpp is None or ppn is None:
                return
            d = v.get("date")
            cands = [s for s in schemes.values() if not d or (s.get("valid_from", "0") <= d <= s.get("valid_to", "9999"))] \
                or list(schemes.values())
            if not any(abs(round(dpp * s["rate"]) - ppn) <= tol + 1e-6 for s in cands):
                rates = sorted({s["rate"] for s in cands})
                rate = min(rates, key=lambda x: abs(round(dpp * x) - ppn))
                add(Issue("ppn_rate", r["severity"],
                          f"PPN {ppn:,.0f} != DPP {dpp:,.0f} x rate {rates} for the document date", ["ppn", "dpp"],
                          _expected("ppn", round(dpp * rate), ppn, tol,
                                    [_term(dpp, field="dpp"), _term(rate, "x", label="rate")])))
        else:
            tax, sub = v.get("tax"), v.get("subtotal")
            if not tax or sub is None:
                return
            base = sub - (v.get("discount") or 0)
            effective = {s["rate"] * s["dpp_factor"] for s in schemes.values()} | {s["rate"] for s in schemes.values()}
            if base > 0 and not any(abs(base * e - tax) <= max(tol, 0.001 * tax) + 0.01 for e in effective):
                rate = min(effective, key=lambda e: abs(base * e - tax))
                add(Issue("ppn_rate", "warning", f"tax {tax:,.2f} is not a known PPN rate of {base:,.2f}", ["tax"],
                          _expected("tax", base * rate, tax, max(tol, 0.001 * tax) + 0.01, [
                              _term(sub, field="subtotal"), _term(v.get("discount") or 0, "-", field="discount"),
                              _term(round(rate, 6), "x", label="rate")])))
