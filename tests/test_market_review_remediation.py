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


class TestMarketReviewBehavioralCoverage(unittest.TestCase):
    """Behavioral tests covering timeout bounds, circuit breaker cascades, cache freshness, policy contracts, and cancellation."""

    def test_provider_blocking_within_timeout(self):
        """Test provider hanging beyond per-provider timeout triggers timeout failure cleanly."""
        manager = DataFetcherManager()
        budget = MarketReviewExecutionBudget(total_timeout_seconds=25.0, provider_timeout_seconds=0.2)

        mock_hanging_fetcher = MagicMock()
        mock_hanging_fetcher.name = "efinance"
        def hang_get_indices():
            time.sleep(1.0)
            return []
        mock_hanging_fetcher.get_main_indices.side_effect = hang_get_indices

        with patch.object(manager, "_get_fetchers_snapshot", return_value=[mock_hanging_fetcher]):
            t0 = time.monotonic()
            result = manager.get_main_indices(region="cn", budget=budget)
            elapsed = time.monotonic() - t0

        self.assertLess(elapsed, 0.9)  # Stopped by 0.2s timeout
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.attempts[0].status, "timeout")

    def test_eastmoney_circuit_breaker_skips_subsequent_calls(self):
        """Test EastMoney failure in indices trips circuit breaker, causing sector & concept to skip EastMoney."""
        DataFetcherManager.clear_concept_rankings_cache_for_tests()
        manager = DataFetcherManager()
        budget = MarketReviewExecutionBudget(total_timeout_seconds=10.0, provider_timeout_seconds=0.2)

        mock_em_fetcher = MagicMock()
        mock_em_fetcher.name = "efinance"
        def fail_indices(region="cn"):
            raise RuntimeError("Connection reset by peer")
        mock_em_fetcher.get_main_indices.side_effect = fail_indices

        with patch.object(manager, "_get_fetchers_snapshot", return_value=[mock_em_fetcher]):
            # 1. Fetch indices fails & trips eastmoney circuit
            indices_res = manager.get_main_indices(region="cn", budget=budget)
            self.assertTrue(budget.is_upstream_tripped("eastmoney"))

            # 2. Fetch sector rankings immediately skips efinance without calling get_sector_rankings
            sector_res = manager.get_sector_rankings(5, budget=budget)
            self.assertEqual(sector_res.status, "unavailable")
            self.assertEqual(sector_res.attempts[0].error_type, "upstream_circuit_open")
            mock_em_fetcher.get_sector_rankings.assert_not_called()

            # 3. Fetch concept rankings immediately skips efinance without calling get_concept_rankings
            concept_res = manager.get_concept_rankings(5, budget=budget)
            self.assertEqual(concept_res.status, "unavailable")
            self.assertEqual(concept_res.attempts[0].error_type, "upstream_circuit_open")
            mock_em_fetcher.get_concept_rankings.assert_not_called()

    def test_stale_cache_behavior_across_policies(self):
        """Test stale cache is rejected under required policy and accepted under optional policy."""
        from src.services.daily_market_context import DailyMarketContextService

        service = DailyMarketContextService(db_manager=MagicMock())
        stale_created_at = datetime.now()
        # Stale context created 2 hours ago
        stale_created_at = datetime.fromtimestamp(time.time() - 7200)
        stale_context = DailyMarketContext(
            region="cn",
            trade_date=datetime.now().date(),
            summary="stale summary",
            source="history",
            status="fresh",
            created_at=stale_created_at,
        )

        config = Config(market_review_allow_stale_cache_seconds=3600)
        notifier = MagicMock()

        with patch.object(service, "_load_same_day_history", return_value=stale_context):
            # Optional mode accepts stale cache
            opt_ctx = service.get_context(
                region="cn",
                config=config,
                notifier=notifier,
                allow_generate=False,
                market_context_policy="optional",
            )
            self.assertIsNotNone(opt_ctx)

            # Required mode rejects stale cache and raises error when allow_generate=False
            with self.assertRaises(MarketReviewDataUnavailableError):
                service.get_context(
                    region="cn",
                    config=config,
                    notifier=notifier,
                    allow_generate=False,
                    market_context_policy="required",
                )

    def test_disabled_policy_returns_disabled_context_immediately(self):
        """Test disabled market context policy skips fetching and returns disabled context."""
        from src.services.daily_market_context import DailyMarketContextService

        service = DailyMarketContextService(db_manager=MagicMock())
        config = Config(market_context_policy="disabled")
        ctx = service.get_context(
            region="cn",
            config=config,
            notifier=MagicMock(),
        )
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx.status, "disabled")
        self.assertEqual(ctx.source, "disabled")

    def test_mid_flight_cancellation_suppresses_persistence_and_notification(self):
        """Test cancellation checkpoint during market review raises TaskCancelledError and suppresses reports/notifications."""
        from src.core.market_review import run_market_review
        from data_provider.base import TaskCancelledError

        config = Config(market_review_realtime_mode="bounded")
        notifier = MagicMock()
        analyzer = MagicMock()

        # Cancellation token returns True on second check
        call_count = 0
        def cancel_checker():
            nonlocal call_count
            call_count += 1
            return call_count >= 2

        with self.assertRaises(TaskCancelledError):
            run_market_review(
                notifier=notifier,
                analyzer=analyzer,
                config=config,
                send_notification=True,
                override_region="cn",
                trigger_source="api",
                cancellation_fn=cancel_checker,
            )

        notifier.send_report.assert_not_called()

    def test_strict_mode_failure_suppresses_notification_and_report_saving(self):
        """Test strict mode failure raises exception and suppresses report saving and notifications."""
        from src.core.market_review import run_market_review

        config = Config(
            market_review_realtime_mode="strict",
            market_review_total_timeout_seconds=5,
            market_review_provider_timeout_seconds=1,
        )
        notifier = MagicMock()

        with patch("src.market_analyzer.DataFetcherManager.get_main_indices") as mock_idx, \
             patch("src.market_analyzer.DataFetcherManager.get_market_stats") as mock_stats, \
             patch("src.market_analyzer.DataFetcherManager.get_sector_rankings") as mock_sec, \
             patch("src.market_analyzer.DataFetcherManager.get_concept_rankings") as mock_con:
            mock_idx.return_value = MarketDataResult(status="unavailable")
            mock_stats.return_value = MarketDataResult(status="unavailable")
            mock_sec.return_value = MarketDataResult(status="unavailable")
            mock_con.return_value = MarketDataResult(status="unavailable")

            with self.assertRaises(MarketReviewDataUnavailableError):
                run_market_review(
                    notifier=notifier,
                    config=config,
                    send_notification=True,
                    override_region="cn",
                    trigger_source="api",
                )

        notifier.send_report.assert_not_called()
        notifier.save_report_to_file.assert_not_called()


if __name__ == "__main__":
    unittest.main()

