"""Image degradations that simulate phone photos and bad scans."""
from __future__ import annotations

import random

import cv2
import numpy as np

LEVELS = ("clean", "light", "medium", "heavy")


def _rotate(img: np.ndarray, angle: float) -> np.ndarray:
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    m[0, 2] += nw / 2 - w / 2
    m[1, 2] += nh / 2 - h / 2
    return cv2.warpAffine(img, m, (nw, nh), borderValue=(255, 255, 255))


def _perspective(img: np.ndarray, rng: random.Random, strength: float) -> np.ndarray:
    h, w = img.shape[:2]
    pad = int(0.06 * max(h, w))
    img = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(205, 200, 190))
    h2, w2 = img.shape[:2]
    src = np.float32([[0, 0], [w2, 0], [w2, h2], [0, h2]])
    j = lambda: rng.uniform(0, strength) * min(w2, h2)  # noqa: E731
    dst = np.float32([[j(), j()], [w2 - j(), j()], [w2 - j(), h2 - j()], [j(), h2 - j()]])
    m = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(img, m, (w2, h2), borderValue=(90, 85, 80))


def _noise(img: np.ndarray, rng: random.Random, sigma: float) -> np.ndarray:
    gen = np.random.default_rng(rng.randint(0, 2**31))
    noise = gen.normal(0, sigma, img.shape)
    return np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)


def _shadow(img: np.ndarray, rng: random.Random) -> np.ndarray:
    h, w = img.shape[:2]
    grad = np.linspace(rng.uniform(0.65, 0.85), 1.0, w, dtype=np.float32)
    if rng.random() < 0.5:
        grad = grad[::-1]
    return np.clip(img.astype(np.float32) * grad[None, :, None], 0, 255).astype(np.uint8)


def _jpeg(img: np.ndarray, quality: int) -> np.ndarray:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def degrade(img: np.ndarray, level: str, rng: random.Random) -> tuple[np.ndarray, dict]:
    """Return degraded BGR image and a description of what was applied."""
    ops: dict = {"level": level}
    if level == "clean":
        return img, ops
    if level == "light":
        angle = rng.uniform(-2, 2)
        img = _rotate(img, angle)
        img = _jpeg(img, 80)
        ops.update(rotate=round(angle, 2), jpeg=80)
    elif level == "medium":
        angle = rng.uniform(-5, 5)
        img = _rotate(img, angle)
        k = rng.choice([3, 5])
        img = cv2.GaussianBlur(img, (k, k), 0)
        img = _noise(img, rng, 10)
        img = _jpeg(img, 60)
        ops.update(rotate=round(angle, 2), blur=k, noise=10, jpeg=60)
    elif level == "heavy":
        if rng.random() < 0.5:  # phone photo
            img = _perspective(img, rng, 0.05)
            img = _shadow(img, rng)
            ops.update(perspective=0.05, shadow=True)
        else:  # bad scan: greyscale, low contrast, speckles
            g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            g = cv2.convertScaleAbs(g, alpha=0.7, beta=50)
            img = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
            ops.update(scan=True)
        angle = rng.uniform(-6, 6)
        img = _rotate(img, angle)
        if rng.random() < 0.15:
            img = cv2.rotate(img, cv2.ROTATE_180 if rng.random() < 0.5 else cv2.ROTATE_90_CLOCKWISE)
            ops["orientation"] = True
        k = rng.choice([3, 5])
        img = cv2.GaussianBlur(img, (k, k), 0)
        img = _noise(img, rng, 14)
        h, w = img.shape[:2]
        scale = rng.uniform(0.6, 0.8)
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        img = _jpeg(img, 40)
        ops.update(rotate=round(angle, 2), blur=k, noise=14, downscale=round(scale, 2), jpeg=40)
    return img, ops
