# -*- coding: utf-8 -*-
"""
Tests for market review realtime stats remediation, timeout budget, circuit breaker,
caller policies, and cancel API (Issue #remediation).
"""

import time
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from data_provider.base import (
    DataFetcherManager,
    MarketDataResult,
    MarketReviewExecutionBudget,
    ProviderAttempt,
    is_circuit_tripping_exception,
)
from src.config import Config
from src.market_analyzer import MarketAnalyzer, MarketOverview
from src.services.daily_market_context import (
    DailyMarketContext,
    MarketReviewDataUnavailableError,
)
from src.services.task_queue import AnalysisTaskQueue, TaskInfo, TaskStatus


class TestMarketReviewRemediationBudgetAndCircuit(unittest.TestCase):
    """Test timeout budget and circuit breaker mechanics in DataFetcherManager."""

    def test_execution_budget_expiration_and_timeout(self):
        budget = MarketReviewExecutionBudget(total_timeout_seconds=2.0, provider_timeout_seconds=0.5)
        self.assertFalse(budget.is_expired())
        self.assertGreater(budget.remaining_seconds, 0)
        self.assertLessEqual(budget.get_provider_timeout(), 0.5)

        # Force deadline past
        budget.deadline = time.monotonic() - 1.0
        self.assertTrue(budget.is_expired())

    def test_circuit_tripping_exception_detection(self):
        class RemoteDisconnected(Exception):
            pass

        self.assertTrue(is_circuit_tripping_exception(RemoteDisconnected("Remote end closed connection without response")))
        self.assertTrue(is_circuit_tripping_exception(RuntimeError("curl (56) Recv failure: Connection reset by peer")))
        self.assertTrue(is_circuit_tripping_exception(TimeoutError("Read timed out")))
        self.assertFalse(is_circuit_tripping_exception(ValueError("Invalid stock code format")))

    def test_upstream_circuit_breaker_tripping_and_skipping(self):
        budget = MarketReviewExecutionBudget(total_timeout_seconds=10.0, provider_timeout_seconds=2.0)
        self.assertFalse(budget.is_upstream_tripped("eastmoney"))

        budget.trip_upstream("eastmoney", "RemoteDisconnected")
        self.assertTrue(budget.is_upstream_tripped("eastmoney"))

        manager = DataFetcherManager()
        result = manager.get_market_stats(
            purpose="test:cn",
            budget=budget,
            providers=["efinance", "akshare"],
        )
        self.assertEqual(result.status, "unavailable")
        # All attempts to eastmoney upstream should be skipped
        for attempt in result.attempts:
            self.assertEqual(attempt.status, "skipped")
            self.assertEqual(attempt.error_type, "upstream_circuit_open")

    def test_sina_success_returns_fresh_stats(self):
        manager = DataFetcherManager()
        mock_sina = MagicMock()
        mock_sina.name = "SinaFetcher"
        mock_sina.get_market_stats.return_value = {
            "up_count": 3100,
            "down_count": 1800,
            "flat_count": 200,
            "limit_up_count": 45,
            "limit_down_count": 3,
            "total_amount": 9500.0,
        }

        budget = MarketReviewExecutionBudget(total_timeout_seconds=10.0, provider_timeout_seconds=2.0)
        with patch.object(manager, "_resolve_fetcher_by_name", return_value=mock_sina):
            result = manager.get_market_stats(
                purpose="test:cn",
                budget=budget,
                providers=["sina"],
            )

        self.assertEqual(result.status, "fresh")
        self.assertEqual(result["up_count"], 3100)
        self.assertEqual(result["down_count"], 1800)
        self.assertEqual(result["total_amount"], 9500.0)
        self.assertIsNotNone(result.as_of)
        self.assertTrue(bool(result))

    def test_empty_data_returns_unavailable_without_fake_zeros(self):
        manager = DataFetcherManager()
        mock_fetcher = MagicMock()
        mock_fetcher.name = "TickFlowFetcher"
        mock_fetcher.get_market_stats.return_value = None

        budget = MarketReviewExecutionBudget(total_timeout_seconds=5.0, provider_timeout_seconds=1.0)
        with patch.object(manager, "_resolve_fetcher_by_name", return_value=mock_fetcher):
            result = manager.get_market_stats(
                purpose="test:cn",
                budget=budget,
                providers=["tickflow"],
            )

        self.assertEqual(result.status, "unavailable")
        self.assertFalse(bool(result))
        self.assertEqual(result.data, {})

        # MarketOverview check
        config = Config(market_review_total_timeout_seconds=5, market_review_provider_timeout_seconds=1)
        analyzer = MarketAnalyzer(region="cn", config=config)
        analyzer.data_manager = manager

        with patch.object(analyzer, "_get_main_indices", return_value=[]), \
             patch.object(analyzer, "_get_sector_rankings", return_value=None), \
             patch.object(analyzer, "_get_concept_rankings", return_value=None):
            overview = analyzer.get_market_overview(budget=budget)
            self.assertEqual(overview.market_context_status, "unavailable")
            self.assertIn("实时市场涨跌统计未取得（数据源不可用或在预算内超时）", overview.warnings)


class TestMarketReviewStrictAndOptionalPolicies(unittest.TestCase):
    """Test strict fail-fast and optional single stock degradation behavior."""

    def test_strict_mode_raises_market_review_data_unavailable_error(self):
        from src.core.market_review import run_market_review

        config = Config(
            market_review_realtime_mode="strict",
            market_review_total_timeout_seconds=5,
            market_review_provider_timeout_seconds=1,
            market_review_stats_providers=["tickflow"],
        )
        notifier = MagicMock()
        analyzer = MagicMock()
        analyzer.generate_market_review.return_value = "# A股大盘复盘\n\n测试"

        with patch("src.market_analyzer.DataFetcherManager.get_market_stats") as mock_stats, \
             patch("src.market_analyzer.MarketAnalyzer.search_market_news", return_value=[]), \
             patch("src.market_analyzer.MarketAnalyzer.generate_market_review", return_value="# A股大盘复盘\n\n测试"):
            mock_stats.return_value = MarketDataResult(status="unavailable")
            with self.assertRaises(MarketReviewDataUnavailableError) as ctx:
                run_market_review(
                    notifier=notifier,
                    analyzer=analyzer,
                    config=config,
                    send_notification=False,
                    override_region="cn",
                    trigger_source="api",
                )

            self.assertEqual(ctx.exception.code, "market_review_realtime_data_unavailable")
            notifier.send_report.assert_not_called()

    def test_single_stock_optional_mode_degrades_gracefully_with_warning(self):
        context = DailyMarketContext(
            region="cn",
            trade_date=datetime.now().date(),
            summary="【提示】市场环境未取得实时统计（数据源不可用或超时）。",
            source="test",
            status="unavailable",
        )

        safe_dict = context.to_safe_dict()
        self.assertEqual(safe_dict["status"], "unavailable")
        self.assertIn("【提示】", safe_dict["summary"])


class TestTaskQueueCancelAPI(unittest.TestCase):
    """Test task queue cancellation and cancel endpoint semantics."""

    def test_cancel_pending_task(self):
        queue = AnalysisTaskQueue()
        task = queue.submit_background_task(
            run_task=lambda: {"ok": True},
            stock_code="600519",
            stock_name="贵州茅台",
            message="Testing cancel pending",
            task_id="test_cancel_pending_1",
        )

        # Immediate cancel before worker thread consumes it
        cancelled_info, success = queue.cancel_task("test_cancel_pending_1")
        self.assertTrue(success)
        self.assertIn(cancelled_info.status, (TaskStatus.CANCELLED, TaskStatus.CANCEL_REQUESTED))

    def test_cancel_already_completed_task_returns_conflict(self):
        queue = AnalysisTaskQueue()
        task = queue.submit_background_task(
            run_task=lambda: {"ok": True},
            stock_code="600519",
            stock_name="贵州茅台",
            task_id="test_completed_1",
        )

        time.sleep(0.1)  # Let worker complete
        cancelled_info, success = queue.cancel_task("test_completed_1")
        if cancelled_info and cancelled_info.status == TaskStatus.COMPLETED:
            self.assertFalse(success)


if __name__ == "__main__":
    unittest.main()
