from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from data_provider.akshare_fetcher import AkshareFetcher


class _AkshareThsIndustryStub:
    @staticmethod
    def stock_board_industry_summary_ths() -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"板块": "行业甲", "涨跌幅": 3.2},
                {"板块": "行业乙", "涨跌幅": -1.5},
                {"板块": "行业丙", "涨跌幅": 0.4},
            ]
        )

    @staticmethod
    def stock_board_industry_name_em() -> pd.DataFrame:
        raise AssertionError("EastMoney fallback must not run when THS succeeds")


def test_sector_rankings_prefers_ths_industry_summary(monkeypatch) -> None:
    import akshare

    monkeypatch.setattr(akshare, "stock_board_industry_summary_ths", _AkshareThsIndustryStub.stock_board_industry_summary_ths)
    monkeypatch.setattr(akshare, "stock_board_industry_name_em", _AkshareThsIndustryStub.stock_board_industry_name_em)

    fetcher = AkshareFetcher()
    monkeypatch.setattr(fetcher, "_enforce_rate_limit", lambda: None)
    top, bottom = fetcher.get_sector_rankings(n=1)

    assert top == [{"name": "行业甲", "change_pct": 3.2}]
    assert bottom == [{"name": "行业乙", "change_pct": -1.5}]


def test_concept_rankings_use_instock_ths_by_default(monkeypatch) -> None:
    """THS success must not touch AkShare's EastMoney concept endpoint."""
    import akshare
    import data_provider.akshare_fetcher as fetcher_module
    import data_provider.instock_concept_rank_adapter as adapter

    monkeypatch.setattr(
        fetcher_module,
        "get_config",
        lambda: SimpleNamespace(
            enable_eastmoney_patch=False,
            market_review_concept_rank_source="ths",
            market_review_provider_timeout_seconds=3,
        ),
    )
    monkeypatch.setattr(
        adapter,
        "get_ths_concept_rankings",
        lambda n, timeout: ([{"name": "题材甲", "change_pct": 4.2}], [{"name": "题材乙", "change_pct": -2.1}]),
    )
    monkeypatch.setattr(
        akshare,
        "stock_board_concept_name_em",
        lambda: (_ for _ in ()).throw(AssertionError("EastMoney must not run")),
    )

    fetcher = AkshareFetcher()
    assert fetcher.get_concept_rankings(n=1) == (
        [{"name": "题材甲", "change_pct": 4.2}],
        [{"name": "题材乙", "change_pct": -2.1}],
    )
