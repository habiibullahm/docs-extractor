"""Pydantic v2 schemas for extracted documents.

Every leaf field is an ``Extracted[T]``: a value plus a confidence in [0, 1].
A field that is not present in the document has ``value=None``; values are never
invented. Ground-truth files use the same structure through ``plain()``, which
strips confidences.
"""
from __future__ import annotations

from typing import Any, Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

T = TypeVar("T")

DocType = Literal["faktur_pajak", "invoice", "receipt", "other"]
DOC_TYPES: tuple[str, ...] = ("faktur_pajak", "invoice", "receipt")


class Extracted(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid")

    value: Optional[T] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp(cls, v: Any) -> float:
        try:
            return min(1.0, max(0.0, float(v)))
        except (TypeError, ValueError):
            return 0.0


Text = Extracted[str]
Num = Extracted[float]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Party(_Base):
    name: Text = Field(default_factory=Text)
    npwp: Text = Field(default_factory=Text, description="Digits only (15 or 16 digits)")
    nik: Text = Field(default_factory=Text, description="16-digit NIK, digits only")
    address: Text = Field(default_factory=Text)


class LineItem(_Base):
    description: Text = Field(default_factory=Text)
    quantity: Num = Field(default_factory=Num)
    unit_price: Num = Field(default_factory=Num)
    discount: Num = Field(default_factory=Num)
    total: Num = Field(default_factory=Num)
    is_adjustment: bool = False


class BankAccount(_Base):
    bank_name: Text = Field(default_factory=Text)
    account_number: Text = Field(default_factory=Text)
    account_name: Text = Field(default_factory=Text)


class _Document(_Base):
    document_inconsistent: bool = Field(
        default=False,
        description="True when the printed document itself is inconsistent (e.g. printed total != sum of items)",
    )


class FakturPajak(_Document):
    doc_type: Literal["faktur_pajak"] = "faktur_pajak"
    tax_invoice_number: Text = Field(default_factory=Text, description="Nomor seri faktur pajak (normalized to digits)")
    date: Text = Field(default_factory=Text, description="ISO date YYYY-MM-DD")
    seller: Party = Field(default_factory=Party)
    buyer: Party = Field(default_factory=Party)
    line_items: list[LineItem] = Field(default_factory=list)
    total: Num = Field(default_factory=Num, description="Harga Jual / Penggantian: sum of line item totals")
    discount: Num = Field(default_factory=Num, description="Dikurangi Potongan Harga")
    dpp: Num = Field(default_factory=Num, description="Dasar Pengenaan Pajak")
    ppn: Num = Field(default_factory=Num, description="Jumlah PPN")
    ppnbm: Num = Field(default_factory=Num, description="Jumlah PPnBM")


class Invoice(_Document):
    doc_type: Literal["invoice"] = "invoice"
    invoice_number: Text = Field(default_factory=Text)
    date: Text = Field(default_factory=Text, description="ISO date YYYY-MM-DD")
    due_date: Text = Field(default_factory=Text, description="ISO date YYYY-MM-DD")
    vendor: Party = Field(default_factory=Party)
    customer: Party = Field(default_factory=Party)
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: Num = Field(default_factory=Num)
    discount: Num = Field(default_factory=Num)
    tax: Num = Field(default_factory=Num)
    total: Num = Field(default_factory=Num)
    currency: Text = Field(default_factory=Text, description="ISO 4217 code, e.g. IDR")
    bank_account: BankAccount = Field(default_factory=BankAccount)


class Receipt(_Document):
    doc_type: Literal["receipt"] = "receipt"
    store_name: Text = Field(default_factory=Text)
    store_address: Text = Field(default_factory=Text)
    date: Text = Field(default_factory=Text, description="ISO date YYYY-MM-DD")
    time: Text = Field(default_factory=Text, description="HH:MM or HH:MM:SS")
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: Num = Field(default_factory=Num)
    discount: Num = Field(default_factory=Num)
    tax: Num = Field(default_factory=Num)
    service_charge: Num = Field(default_factory=Num)
    total: Num = Field(default_factory=Num)
    payment_method: Text = Field(default_factory=Text)


_MODELS: dict[str, type[_Document]] = {
    "faktur_pajak": FakturPajak,
    "invoice": Invoice,
    "receipt": Receipt,
}


def model_for(doc_type: str) -> type[_Document]:
    try:
        return _MODELS[doc_type]
    except KeyError as e:
        raise ValueError(f"unsupported doc_type: {doc_type}") from e


def _is_extracted(d: Any) -> bool:
    return isinstance(d, dict) and set(d.keys()) <= {"value", "confidence"} and "value" in d


def plain(obj: BaseModel | dict[str, Any]) -> dict[str, Any]:
    """Strip confidences: {'total': {'value': 5, 'confidence': .9}} -> {'total': 5}."""
    data = obj.model_dump() if isinstance(obj, BaseModel) else obj

    def walk(x: Any) -> Any:
        if _is_extracted(x):
            return x["value"]
        if isinstance(x, dict):
            return {k: walk(v) for k, v in x.items()}
        if isinstance(x, list):
            return [walk(v) for v in x]
        return x

    return walk(data)


def from_plain(doc_type: str, values: dict[str, Any], confidence: float = 1.0) -> _Document:
    """Build a model from plain values (used for ground truth and tests)."""
    model = model_for(doc_type)

    def wrap(schema_obj: type[BaseModel], data: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, field in schema_obj.model_fields.items():
            if name not in data:
                continue
            v = data[name]
            ann = field.annotation
            origin = getattr(ann, "__pydantic_generic_metadata__", {}).get("origin")
            if origin is Extracted:
                out[name] = {"value": v, "confidence": confidence if v is not None else 0.0}
            elif isinstance(ann, type) and issubclass(ann, BaseModel):
                out[name] = wrap(ann, v or {})
            elif name == "line_items":
                out[name] = [wrap(LineItem, it) for it in v]
            else:
                out[name] = v
        return out

    return model.model_validate(wrap(model, values))
