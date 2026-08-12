from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from data_provider import instock_concept_rank_adapter as adapter


def test_adapter_normalizes_instock_ths_dataframe(monkeypatch) -> None:
    module = SimpleNamespace(
        stock_concept_theme_rank=lambda **_: pd.DataFrame(
            [
                {"题材": "题材甲", "涨跌幅": 3.1},
                {"题材": "题材乙", "涨跌幅": -1.8},
                {"题材": "题材丙", "涨跌幅": 0.2},
            ]
        )
    )
    monkeypatch.setattr(adapter, "_load_instock_module", lambda: module)

    top, bottom = adapter.get_ths_concept_rankings(n=1, timeout=3)

    assert top == [{"name": "题材甲", "change_pct": 3.1}]
    assert bottom == [{"name": "题材乙", "change_pct": -1.8}]
