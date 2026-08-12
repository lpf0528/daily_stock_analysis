# -*- coding: utf-8 -*-
"""Integration tests for legacy non-/v1 API route compatibility and auth middleware protection."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import src.auth as auth
from api.app import create_app
from src.config import Config


def _reset_auth_globals() -> None:
    auth._auth_enabled = None
    auth._session_secret = None
    auth._password_hash_salt = None
    auth._password_hash_stored = None
    auth._rate_limit = {}


class LegacyApiRouteCompatTestCase(unittest.TestCase):
    """Test legacy /api/... routes without /v1 prefix."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._temp_dir = tempfile.TemporaryDirectory()
        cls.data_dir = Path(cls._temp_dir.name)
        cls.env_path = cls.data_dir / ".env"
        cls.env_path.write_text(
            "STOCK_LIST=600519\nGEMINI_API_KEY=test\nADMIN_AUTH_ENABLED=false\n",
            encoding="utf-8",
        )
        os.environ["ENV_FILE"] = str(cls.env_path)
        os.environ["DATABASE_PATH"] = str(cls.data_dir / "test.db")
        Config.reset_instance()

        cls._data_dir_patcher = patch.object(
            auth, "_get_data_dir", return_value=cls.data_dir
        )
        cls._data_dir_patcher.start()

        _reset_auth_globals()
        auth.refresh_auth_state()

        cls.client = TestClient(create_app(static_dir=cls.data_dir))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._data_dir_patcher.stop()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        _reset_auth_globals()
        cls._temp_dir.cleanup()

    def setUp(self) -> None:
        _reset_auth_globals()
        self.env_path.write_text(
            "STOCK_LIST=600519\nGEMINI_API_KEY=test\nADMIN_AUTH_ENABLED=false\n",
            encoding="utf-8",
        )
        Config.reset_instance()
        auth.refresh_auth_state()

    def test_legacy_health_routes(self) -> None:
        """Verify /health, /api/health, and /api/v1/health all return 200 OK."""
        for path in ("/health", "/api/health", "/api/v1/health"):
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200, f"Failed for {path}")
            data = resp.json()
            self.assertEqual(data.get("status"), "ok")

    def test_legacy_auth_status_route(self) -> None:
        """Verify legacy /api/auth/status returns same output as /api/v1/auth/status."""
        resp_v1 = self.client.get("/api/v1/auth/status")
        resp_legacy = self.client.get("/api/auth/status")

        self.assertEqual(resp_v1.status_code, 200)
        self.assertEqual(resp_legacy.status_code, 200)
        self.assertEqual(resp_v1.json(), resp_legacy.json())

    def test_legacy_system_config_route(self) -> None:
        """Verify legacy /api/system/config returns 200 when auth is disabled."""
        resp = self.client.get("/api/system/config")
        self.assertEqual(resp.status_code, 200)

    def test_legacy_routes_protected_when_auth_enabled(self) -> None:
        """Verify legacy /api/* routes require authentication when ADMIN_AUTH_ENABLED=true."""
        self.env_path.write_text(
            "STOCK_LIST=600519\nGEMINI_API_KEY=test\nADMIN_AUTH_ENABLED=true\n",
            encoding="utf-8",
        )
        Config.reset_instance()
        auth.refresh_auth_state()
        auth.set_initial_password("passwd123")

        # Public exempt legacy routes should remain accessible
        resp_exempt = self.client.get("/api/auth/status")
        self.assertEqual(resp_exempt.status_code, 200)

        # Protected legacy routes without session should return 401
        resp_protected = self.client.get("/api/system/config")
        self.assertEqual(resp_protected.status_code, 401)
        self.assertEqual(resp_protected.json().get("error"), "unauthorized")

    def test_unregistered_legacy_api_route_returns_404_json(self) -> None:
        """Verify unknown /api/* routes return 404 JSON instead of HTML SPA fallback."""
        resp = self.client.get("/api/nonexistent_route")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.headers.get("content-type"), "application/json")
        body = resp.json()
        self.assertTrue("detail" in body or "error" in body)
