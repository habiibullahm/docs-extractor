"""Streamlit review UI: upload / try samples, review queue (image left, fields right), metrics."""
from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx
import streamlit as st

API = os.environ.get("DOCEX_API_URL", "http://localhost:8000")
LOW_CONF = float(os.environ.get("DOCEX_LOW_CONF", "0.8"))

st.set_page_config(page_title="docex — review", layout="wide")


def api(method: str, path: str, **kw) -> Any:
    r = httpx.request(method, f"{API}{path}", timeout=120, **kw)
    if r.status_code >= 400:
        st.error(f"{r.status_code}: {r.text[:300]}")
        return None
    return r.json() if "json" in r.headers.get("content-type", "") else r.content


def wait_for(doc_id: str, timeout: float = 180) -> dict[str, Any] | None:
    t0 = time.time()
    with st.spinner("Processing document..."):
        while time.time() - t0 < timeout:
            d = api("GET", f"/documents/{doc_id}")
            if d and d["status"] in ("done", "failed"):
                return d
            time.sleep(1.0)
    return None


def leaf_rows(ex: dict[str, Any], prefix: str = "") -> list[tuple[str, Any, float]]:
    rows = []
    for k, v in ex.items():
        if k in ("doc_type", "line_items"):
            continue
        if isinstance(v, dict) and "value" in v and "confidence" in v:
            rows.append((prefix + k, v["value"], v["confidence"]))
        elif isinstance(v, dict):
            rows += leaf_rows(v, prefix + k + ".")
    return rows


def set_path(d: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    for p in parts[:-1]:
        d = d.setdefault(p, {})
    d[parts[-1]] = value


def show_result(d: dict[str, Any], editable: bool) -> None:
    left, right = st.columns([1, 1])
    with left:
        img = api("GET", f"/documents/{d['id']}/preview")
        if img:
            st.image(img, use_container_width=True)
    with right:
        route = d.get("route")
        badge = "🟢 auto-approved" if route == "auto_approved" else "🟠 needs review"
        st.markdown(f"**{d.get('doc_type')}** · {badge} · score **{d.get('score', 0):.2f}** · approach {d['approach']}"
                    f"{' · repaired' if d.get('repaired') else ''}")
        st.caption(d.get("route_reason") or "")
        for issue in d.get("issues") or []:
            (st.error if issue["severity"] == "error" else st.warning)(f"{issue['rule']}: {issue['message']}")
        ex = d.get("extraction") or {}
        edited: dict[str, Any] = {}
        st.markdown(f"Fields with confidence below {LOW_CONF:.1f} are marked ⚠️")
        for path, value, conf in leaf_rows(ex):
            low = conf < LOW_CONF or value is None
            label = f"{'⚠️ ' if low else ''}{path} · conf {conf:.2f}"
            if editable:
                txt = st.text_input(label, "" if value is None else str(value), key=f"{d['id']}:{path}")
                edited[path] = txt
            else:
                st.markdown(f"{'⚠️ ' if low else ''}`{path}` = **{value}** ({conf:.2f})")
        items = ex.get("line_items") or []
        if items:
            st.markdown("**Line items**")
            table = [{k: (v["value"] if isinstance(v, dict) else v) for k, v in it.items()} for it in items]
            confs = [min((v["confidence"] for v in it.values() if isinstance(v, dict) and v.get("value") is not None),
                         default=0) for it in items]
            for row, c in zip(table, confs):
                row["min_conf"] = round(c, 2)
            if editable:
                table = st.data_editor(table, key=f"{d['id']}:items", num_rows="dynamic")
            else:
                st.dataframe(table)
        u = d.get("usage") or {}
        st.caption(f"latency {d.get('latency_ms', 0) / 1000:.1f}s · cost ${u.get('cost_usd', 0):.5f} · "
                   f"versions {json.dumps(d.get('versions'))}")
        if editable:
            c1, c2, c3 = st.columns(3)
            reviewer = st.text_input("Reviewer", "demo", key=f"{d['id']}:reviewer")
            if c1.button("✅ Approve", key=f"{d['id']}:approve"):
                api("POST", f"/review/{d['id']}", json={"action": "approve", "reviewer": reviewer})
                st.success("Approved")
            if c2.button("✏️ Save correction", key=f"{d['id']}:correct"):
                values: dict[str, Any] = {}
                for path, txt in edited.items():
                    set_path(values, path, txt.strip() or None)
                values["line_items"] = [{k: v for k, v in row.items() if k != "min_conf"} for row in table]
                r = api("POST", f"/review/{d['id']}", json={"action": "correct", "values": values,
                                                            "reviewer": reviewer})
                if r:
                    st.success(f"Saved. Changed fields: {', '.join(r['changed_fields']) or 'none'}")
            if c3.button("🗑 Reject", key=f"{d['id']}:reject"):
                api("POST", f"/review/{d['id']}", json={"action": "reject", "reviewer": reviewer})
                st.info("Rejected")
        with st.expander("Trace / raw JSON"):
            st.json({"trace": d.get("trace"), "values": d.get("values")})


st.sidebar.title("docex")
st.sidebar.caption("Indonesian faktur pajak / invoice / receipt extraction · synthetic demo data only")
health = api("GET", "/health") or {}
st.sidebar.markdown(f"Default approach: **{health.get('default_approach', '?')}**")
page = st.sidebar.radio("Page", ["Try it", "Review queue", "Metrics"])

if page == "Try it":
    st.header("Extract a document")
    st.caption("Do not upload real documents containing personal NPWP/NIK. Use the synthetic samples.")
    samples = api("GET", "/samples") or []
    approach = st.selectbox("Approach", ["default", "A", "B", "C"],
                            help="A = OCR + regex (local), B = OCR + LLM, C = vision LLM")
    ap = None if approach == "default" else approach
    if samples:
        names = [s["name"] for s in samples]
        choice = st.selectbox("Synthetic sample", names, format_func=lambda n: next(
            f"{s['name']} — {s['label']}" for s in samples if s["name"] == n))
        if st.button("Run sample"):
            r = api("POST", f"/samples/{choice}", params={"approach": ap} if ap else None)
            if r:
                st.session_state["last"] = r["job_id"]
    up = st.file_uploader("...or upload a PDF / image", type=["pdf", "png", "jpg", "jpeg", "webp"])
    if up and st.button("Upload & extract"):
        r = api("POST", "/documents", files={"file": (up.name, up.getvalue(), up.type)},
                data={"approach": ap} if ap else None)
        if r:
            st.session_state["last"] = r["job_id"]
    if st.session_state.get("last"):
        d = wait_for(st.session_state["last"])
        if d:
            show_result(d, editable=d.get("route") == "review")

elif page == "Review queue":
    st.header("Review queue")
    queue = api("GET", "/review/queue") or []
    if not queue:
        st.info("Nothing to review. Documents below the score threshold land here.")
    else:
        sel = st.selectbox("Document", [q["id"] for q in queue], format_func=lambda i: next(
            f"{q['filename']} · {q['doc_type']} · score {q['score']:.2f} · {q['route_reason']}" for q in queue
            if q["id"] == i))
        d = api("GET", f"/documents/{sel}")
        if d:
            show_result(d, editable=True)

else:
    st.header("Metrics")
    m = api("GET", "/metrics") or {}
    c = st.columns(4)
    c[0].metric("Documents", m.get("documents", 0))
    ar = m.get("auto_approve_rate")
    c[1].metric("Auto-approve rate", f"{ar * 100:.0f}%" if ar is not None else "–")
    c[2].metric("Latency p50", f"{(m.get('latency_ms_p50') or 0) / 1000:.1f}s")
    c[3].metric("Total cost", f"${m.get('cost_usd_total', 0):.4f}")
    st.json({k: m.get(k) for k in ("by_status", "by_route", "by_doc_type", "reviews")})
    off = m.get("offline_eval") or {}
    if off.get("approaches"):
        st.subheader("Offline evaluation (synthetic dev/test sets)")
        st.dataframe(off["approaches"])
