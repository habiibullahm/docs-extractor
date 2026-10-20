"""Preprocessing: load PDF/image -> page images, fix orientation, deskew, resize."""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

MAX_PAGES = 3


@dataclass
class PreparedDocument:
    pages: list[np.ndarray]                  # BGR images after preprocessing
    source_kind: str                         # "pdf" | "image"
    steps: dict[str, Any] = field(default_factory=dict)

    @property
    def image(self) -> np.ndarray:
        """All pages stacked vertically (most documents here are single-page)."""
        if len(self.pages) == 1:
            return self.pages[0]
        w = max(p.shape[1] for p in self.pages)
        padded = [cv2.copyMakeBorder(p, 0, 0, 0, w - p.shape[1], cv2.BORDER_CONSTANT, value=(255, 255, 255))
                  for p in self.pages]
        return np.vstack(padded)


def _is_pdf(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


def load_pages(data: bytes, dpi: int = 200) -> tuple[list[np.ndarray], str]:
    if _is_pdf(data):
        import pymupdf

        pages = []
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            for page in list(doc)[:MAX_PAGES]:
                pix = page.get_pixmap(dpi=dpi)
                arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
                pages.append(cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR if pix.n == 4 else cv2.COLOR_RGB2BGR))
        return pages, "pdf"
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        from PIL import Image  # fallback for formats OpenCV can't read (webp variants, tiff pages)

        pil = Image.open(io.BytesIO(data)).convert("RGB")
        img = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
    return [img], "image"


def fix_orientation(img: np.ndarray) -> tuple[np.ndarray, int]:
    """Use Tesseract OSD to undo 90/180/270 degree rotations. Returns (image, degrees rotated)."""
    try:
        import pytesseract

        small = _resize_max(img, 2400)
        osd = pytesseract.image_to_osd(small, config="--psm 0", output_type=pytesseract.Output.DICT)
        rotate = int(osd.get("rotate", 0))
        if float(osd.get("orientation_conf", 0)) < 1.0:
            return img, 0
    except Exception:
        return img, 0
    if rotate == 90:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE), 90
    if rotate == 180:
        return cv2.rotate(img, cv2.ROTATE_180), 180
    if rotate == 270:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE), 270
    return img, 0


def estimate_skew(img: np.ndarray, max_angle: float = 8.0, step: float = 0.5) -> float:
    """Projection-profile skew estimate: the angle that makes text rows sharpest."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    small = _resize_max(gray, 900)
    _, bw = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h, w = bw.shape
    center = (w / 2, h / 2)

    def score(angle: float) -> float:
        m = cv2.getRotationMatrix2D(center, angle, 1.0)
        rot = cv2.warpAffine(bw, m, (w, h), flags=cv2.INTER_NEAREST, borderValue=0)
        rows = rot.sum(axis=1, dtype=np.float64)
        return float(np.var(np.diff(rows)))

    angles = np.arange(-max_angle, max_angle + 1e-6, step)
    scores = [score(a) for a in angles]
    best = float(angles[int(np.argmax(scores))])
    fine = np.arange(best - step, best + step + 1e-6, step / 5)
    return float(fine[int(np.argmax([score(a) for a in fine]))])


def rotate_quadrant(img: np.ndarray, degrees: int) -> np.ndarray:
    codes = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
    return cv2.rotate(img, codes[degrees]) if degrees in codes else img


def rotate(img: np.ndarray, angle: float) -> np.ndarray:
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    m[0, 2] += nw / 2 - w / 2
    m[1, 2] += nh / 2 - h / 2
    return cv2.warpAffine(img, m, (nw, nh), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))


def _resize_max(img: np.ndarray, max_side: int) -> np.ndarray:
    h, w = img.shape[:2]
    s = max_side / max(h, w)
    if s >= 1:
        return img
    return cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)


def _upscale_small(img: np.ndarray, min_side: int = 1400) -> np.ndarray:
    h, w = img.shape[:2]
    if max(h, w) >= min_side:
        return img
    s = min_side / max(h, w)
    return cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_CUBIC)


def preprocess(data: bytes, cfg: Optional[dict[str, Any]] = None) -> PreparedDocument:
    cfg = cfg or {}
    pages, kind = load_pages(data, dpi=cfg.get("dpi", 200))
    steps: dict[str, Any] = {"pages": len(pages), "orientation": [], "skew": []}
    out = []
    for img in pages:
        if kind == "image":
            img = _upscale_small(img)
            if cfg.get("fix_orientation", True):
                img, deg = fix_orientation(img)
                steps["orientation"].append(deg)
            if cfg.get("deskew", True):
                angle = estimate_skew(img)
                if abs(angle) >= 0.3:
                    img = rotate(img, angle)
                steps["skew"].append(round(angle, 2))
        img = _resize_max(img, cfg.get("max_side", 2000))
        out.append(img)
    return PreparedDocument(out, kind, steps)


def load_path(path: str | Path, cfg: Optional[dict[str, Any]] = None) -> PreparedDocument:
    return preprocess(Path(path).read_bytes(), cfg)


def to_jpeg_bytes(img: np.ndarray, max_side: int = 1600, quality: int = 85) -> bytes:
    small = _resize_max(img, max_side)
    ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes()
