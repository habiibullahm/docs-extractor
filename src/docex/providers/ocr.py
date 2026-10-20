"""OCR providers (local, open-source): Tesseract and RapidOCR (PaddleOCR models on ONNX)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

# Tesseract starts one OpenMP thread per core; concurrent pages then thrash (50 s+ per page).
os.environ.setdefault("OMP_THREAD_LIMIT", "1")


@dataclass
class OcrLine:
    text: str
    confidence: float  # 0..1
    bbox: tuple[int, int, int, int]  # x, y, w, h


@dataclass
class OcrResult:
    lines: list[OcrLine] = field(default_factory=list)
    engine: str = ""

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    @property
    def mean_confidence(self) -> float:
        if not self.lines:
            return 0.0
        return float(np.mean([line.confidence for line in self.lines]))


class OcrProvider(Protocol):
    name: str

    def ocr(self, image: np.ndarray) -> OcrResult: ...


class TesseractOcr:
    name = "tesseract"

    def __init__(self, languages: str = "ind+eng", psm: int = 6) -> None:
        self.languages = languages
        self.psm = psm

    @property
    def version(self) -> str:
        import pytesseract

        return f"tesseract-{pytesseract.get_tesseract_version()}"

    def ocr(self, image: np.ndarray) -> OcrResult:
        import pytesseract

        data = pytesseract.image_to_data(
            prepare_for_ocr(image), lang=self.languages, config=f"--psm {self.psm} --oem 1 -c preserve_interword_spaces=1",
            output_type=pytesseract.Output.DICT,
        )
        groups: dict[tuple[int, int, int], list[int]] = {}
        for i, word in enumerate(data["text"]):
            if not str(word).strip():
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            groups.setdefault(key, []).append(i)
        lines = []
        for idxs in groups.values():
            idxs.sort(key=lambda i: data["left"][i])
            words, confs = [], []
            prev_right = None
            for i in idxs:
                left = data["left"][i]
                # keep wide gaps visible: they usually separate table columns
                if prev_right is not None and left - prev_right > 2.5 * max(8, data["height"][i]):
                    words.append("  ")
                words.append(str(data["text"][i]))
                prev_right = left + data["width"][i]
                c = float(data["conf"][i])
                if c >= 0:
                    confs.append(c / 100.0)
            text = _join(words)
            x0 = min(data["left"][i] for i in idxs)
            y0 = min(data["top"][i] for i in idxs)
            x1 = max(data["left"][i] + data["width"][i] for i in idxs)
            y1 = max(data["top"][i] + data["height"][i] for i in idxs)
            lines.append(OcrLine(text, float(np.mean(confs)) if confs else 0.0, (x0, y0, x1 - x0, y1 - y0)))
        lines.sort(key=lambda l: (l.bbox[1] + l.bbox[3] / 2, l.bbox[0]))
        return OcrResult(_merge_rows(lines), self.name)


def prepare_for_ocr(image: np.ndarray) -> np.ndarray:
    """Grey image tuned for Tesseract: invert white-on-dark bands, flatten uneven
    illumination (shadows, grey scans), then erase table borders and long rules."""
    import cv2

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image.copy()
    h, w = gray.shape
    # 1) dark bands with light text (coloured headers): invert them
    k = max(31, (min(h, w) // 20) | 1)
    local = cv2.medianBlur(gray, k if k % 2 else k + 1)
    dark = local < 100
    if dark.any():
        gray = np.where(dark, 255 - gray, gray).astype(np.uint8)
    # 2) illumination flattening: divide by a text-free background estimate
    bg = cv2.dilate(gray, np.ones((9, 9), np.uint8))
    bg = cv2.medianBlur(bg, 31)
    flat = cv2.divide(gray, np.maximum(bg, 1), scale=255)
    flat = cv2.normalize(flat, None, 0, 255, cv2.NORM_MINMAX)
    # 3) erase long horizontal / vertical lines
    bw = cv2.adaptiveThreshold(flat, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 15)
    horiz = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, w // 25), 1)))
    vert = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(40, h // 40))))
    mask = cv2.dilate(cv2.bitwise_or(horiz, vert), np.ones((3, 3), np.uint8))
    flat[mask > 0] = 255
    return flat


def _join(words: list[str]) -> str:
    out = ""
    for w in words:
        if w == "  ":
            out += "   "
        else:
            out += (" " if out and not out.endswith("   ") else "") + w
    return out.strip()


def _merge_rows(lines: list[OcrLine]) -> list[OcrLine]:
    """Merge line fragments that sit on the same visual row (e.g. label and value columns)."""
    merged: list[OcrLine] = []
    for line in lines:
        if merged:
            prev = merged[-1]
            pc = prev.bbox[1] + prev.bbox[3] / 2
            lc = line.bbox[1] + line.bbox[3] / 2
            h = max(4, min(prev.bbox[3], line.bbox[3]))
            if abs(pc - lc) < 0.5 * h and line.bbox[0] > prev.bbox[0] + prev.bbox[2] - 2:
                x0 = prev.bbox[0]
                y0 = min(prev.bbox[1], line.bbox[1])
                x1 = line.bbox[0] + line.bbox[2]
                y1 = max(prev.bbox[1] + prev.bbox[3], line.bbox[1] + line.bbox[3])
                n_prev, n_line = len(prev.text), len(line.text)
                conf = (prev.confidence * n_prev + line.confidence * n_line) / max(1, n_prev + n_line)
                merged[-1] = OcrLine(prev.text + "   " + line.text, conf, (x0, y0, x1 - x0, y1 - y0))
                continue
        merged.append(line)
    return merged


class RapidOcr:
    name = "rapidocr"

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR

        self._engine = RapidOCR()

    @property
    def version(self) -> str:
        return "rapidocr-onnxruntime-ppocr"

    def ocr(self, image: np.ndarray) -> OcrResult:
        result, _ = self._engine(image)
        lines = []
        for box, text, score in result or []:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            lines.append(OcrLine(str(text), float(score), (int(min(xs)), int(min(ys)),
                                                          int(max(xs) - min(xs)), int(max(ys) - min(ys)))))
        lines.sort(key=lambda l: (l.bbox[1] + l.bbox[3] / 2, l.bbox[0]))
        return OcrResult(_merge_rows(lines), self.name)


_CACHE: dict[str, Any] = {}


def get_ocr(cfg: dict[str, Any]) -> OcrProvider:
    name = cfg.get("provider", "tesseract")
    if name not in _CACHE:
        if name == "tesseract":
            _CACHE[name] = TesseractOcr(cfg.get("languages", "ind+eng"), int(cfg.get("psm", 6)))
        elif name == "rapidocr":
            _CACHE[name] = RapidOcr()
        else:
            raise ValueError(f"unknown OCR provider {name}")
    return _CACHE[name]
