"""Adapter for the sibling InStock real-time THS concept ranking collector.

The InStock collector owns the anti-bot session preparation for the public
Tonghuashun ranking page.  Keeping that code in one place prevents DSA from
silently treating an AkShare/EastMoney wrapper as an independent fallback.
"""

from __future__ import annotations

import importlib.util
import os
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Dict, List, Tuple

import pandas as pd


def _module_path() -> Path:
    """Return the explicit override or the standard sibling InStock module."""
    configured = os.getenv("INSTOCK_CONCEPT_RANK_MODULE_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (
        Path(__file__).resolve().parents[2]
        / "stock"
        / "instock"
        / "core"
        / "crawling"
        / "stock_concept_rank.py"
    )


@lru_cache(maxsize=1)
def _load_instock_module() -> ModuleType:
    path = _module_path()
    if not path.is_file():
        raise FileNotFoundError(
            "InStock 同花顺题材采集模块不存在: "
            f"{path}. 请设置 INSTOCK_CONCEPT_RANK_MODULE_PATH，或将 stock 与 "
            "daily_stock_analysis 保持为同级目录。"
        )
    spec = importlib.util.spec_from_file_location("_instock_concept_rank", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 InStock 同花顺题材采集模块: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def get_ths_concept_rankings(n: int, timeout: int) -> Tuple[List[Dict], List[Dict]]:
    """Fetch and normalize real-time THS theme rankings for DSA's contract."""
    module = _load_instock_module()
    frame = module.stock_concept_theme_rank(source="ths", timeout=timeout)
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return [], []

    name_col = "题材" if "题材" in frame.columns else "概念"
    change_col = "涨跌幅"
    if name_col not in frame.columns or change_col not in frame.columns:
        raise ValueError(
            "InStock 同花顺题材排行缺少必要字段 "
            f"(现有字段: {', '.join(map(str, frame.columns))})"
        )

    normalized = frame[[name_col, change_col]].copy()
    normalized[change_col] = pd.to_numeric(normalized[change_col], errors="coerce")
    normalized = normalized.dropna(subset=[name_col, change_col])
    if normalized.empty:
        return [], []

    top = normalized.nlargest(n, change_col)
    bottom = normalized.nsmallest(n, change_col)

    def _rows(data: pd.DataFrame) -> List[Dict]:
        return [
            {"name": str(row[name_col]), "change_pct": float(row[change_col])}
            for _, row in data.iterrows()
        ]

    return _rows(top), _rows(bottom)
