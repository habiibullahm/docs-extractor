"""Configuration loading (YAML files in configs/, overridable with env vars)."""
from __future__ import annotations

import copy
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(os.environ.get("DOCEX_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = ROOT / "configs"
PROMPT_DIR = ROOT / "prompts"
DATA_DIR = Path(os.environ.get("DOCEX_DATA_DIR", ROOT / "data"))


def _load(name: str) -> dict[str, Any]:
    with open(CONFIG_DIR / f"{name}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _coerce(value: str) -> Any:
    try:
        return yaml.safe_load(value)
    except yaml.YAMLError:
        return value


def _apply_env_overrides(cfg: dict[str, Any], prefix: str = "DOCEX_") -> dict[str, Any]:
    """DOCEX_LLM__MODEL=claude-haiku-5-5 sets cfg['llm']['model']."""
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        path = key[len(prefix):].lower().split("__")
        if len(path) == 1 and isinstance(cfg.get(path[0]), dict):
            continue
        node = cfg
        for part in path[:-1]:
            if not isinstance(node.get(part), dict):
                break
            node = node[part]
        else:
            if path[-1] in node:
                node[path[-1]] = _coerce(value)
    return cfg


@lru_cache(maxsize=1)
def _base_pipeline() -> dict[str, Any]:
    return _load("pipeline")


def pipeline_config(**overrides: Any) -> dict[str, Any]:
    cfg = _apply_env_overrides(copy.deepcopy(_base_pipeline()))
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


@lru_cache(maxsize=1)
def tax_rules() -> dict[str, Any]:
    return _load("tax_rules")


@lru_cache(maxsize=1)
def validation_config() -> dict[str, Any]:
    return _load("validation")
