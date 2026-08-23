"""Shared test harness: isolated DB per test class, fixture web server, no network."""
from __future__ import annotations
import os, shutil, tempfile, unittest

def _prepare_env(tmpdir: str) -> None:
    # Unit/runtime tests are intentionally SQLite-scoped. PostgreSQL tests opt
    # in explicitly through TEST_DATABASE_URL in tests/test_postgres.py.
    os.environ.pop("DATABASE_URL", None)
    os.environ["APP_ENV"] = "test"
    os.environ["DB_PATH"] = os.path.join(tmpdir, "test.db")
    os.environ["SECRET_KEY"] = "test-secret-key-that-is-long-enough-1234"
    os.environ["LOG_SILENT"] = "1"
    os.environ["ALLOW_PRIVATE_HOSTS"] = "1"      # fixture server lives on 127.0.0.1
    os.environ["HTTP_TIMEOUT_SECONDS"] = "3"
    os.environ["HTTP_RETRIES"] = "1"
    os.environ["ENABLED_SOURCES"] = "demo_kg"
    os.environ["LLM_PROVIDER"] = "local_rules"

class AppTestCase(unittest.TestCase):
    with_fixture_server = False

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="lh-test-")
        _prepare_env(cls.tmpdir)
        from app import config
        from app.db import migrations, sqlite as db
        from app.api import ratelimit
        from app.analysis import http_client
        config.reset_cache()
        config.load(force=True)
        db.close()
        migrations.migrate()
        ratelimit.reset()
        http_client.clear_robots_cache()
        http_client.BUDGET.reset()
        if cls.with_fixture_server:
            from tools.fixture_server import FixtureServer
            cls.web = FixtureServer().start()

    @classmethod
    def tearDownClass(cls):
        from app.db import sqlite as db
        if getattr(cls, "web", None):
            cls.web.stop()
        db.close()
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def make_user(self, email: str = "owner@test.kg", password: str = "Str0ngPass!x"):
        from app.api import auth
        user_id, org_id = auth.register(email, password)
        token, _user = auth.login(email, password)
        return {"user_id": user_id, "org_id": org_id, "token": token, "email": email, "password": password}
