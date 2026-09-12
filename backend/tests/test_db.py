"""Tests for db.py's pure logic, its not_configured path, and the
shared-in-flight-check behavior that prevents accumulating threads.

Deliberately does NOT test the "healthy" branch against a real database
here - that's exercised manually against the actual Docker container
(see docs/technical/local-setup.md) and indirectly through
test_health.py's monkeypatched readiness tests. Keeping this suite free
of a real database dependency is what lets it pass the same way in CI as
on a laptop with Docker running.
"""

from __future__ import annotations

import asyncio

import pytest

from tenderpilot import db
from tenderpilot.config import Settings
from tenderpilot.db import build_database_url, get_database_status


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


def test_build_database_url_is_none_without_a_password() -> None:
    settings = _settings(database_app_password=None)
    assert build_database_url(settings) is None


def test_build_database_url_uses_the_restricted_app_role_not_the_bootstrap_user() -> None:
    settings = _settings(
        database_app_user="tenderpilot_app",
        database_app_password="a-real-looking-password",
        database_host="localhost",
        database_port=5432,
        database_name="tenderpilot",
    )
    url = build_database_url(settings)
    assert url is not None
    assert url.username == "tenderpilot_app"
    assert url.username != "tenderpilot_admin"  # never the bootstrap superuser
    assert url.host == "localhost"
    assert url.port == 5432
    assert url.database == "tenderpilot"


def test_build_database_url_handles_a_password_with_url_special_characters() -> None:
    # The exact kind of value that would have silently corrupted the old
    # f-string-built URL: "@" would be parsed as the start of the host
    # section, "/" as a path separator, etc. URL.create() percent-encodes
    # all of this correctly rather than needing it to be pre-escaped.
    tricky_password = "p@ss/word:with#special?chars&and=more"
    settings = _settings(
        database_app_user="tenderpilot_app",
        database_app_password=tricky_password,
        database_host="localhost",
        database_port=5432,
        database_name="tenderpilot",
    )
    url = build_database_url(settings)
    assert url is not None
    # .password decodes back to the exact original value - proves the
    # encode/decode round-trip is correct, not just "doesn't crash".
    assert url.password == tricky_password
    # str(url) hides the password by default - an accidental log/print
    # of the URL object itself can't leak the credential.
    assert tricky_password not in str(url)
    assert "***" in str(url)
    # The fully rendered connection string (what create_engine actually
    # uses) does contain the real, correctly-escaped password.
    rendered = url.render_as_string(hide_password=False)
    assert "p%40ss%2Fword%3Awith%23special%3Fchars%26and%3Dmore" in rendered


def test_get_database_status_is_not_configured_without_a_password() -> None:
    settings = _settings(database_app_password=None)
    result = asyncio.run(get_database_status(settings))
    assert result == "not_configured"


def test_overlapping_calls_share_one_in_flight_check_instead_of_starting_a_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for the thread-accumulation review finding:
    asyncio.timeout() around asyncio.to_thread() does not stop an
    already-running thread, so a naive implementation could start a new
    one on every call while a previous, still-hanging check lingers in
    the background. This proves at most ONE underlying check ever runs
    at a time, no matter how many overlapping callers arrive - and that
    the system still recovers once that check actually finishes."""
    call_count = 0
    release = asyncio.Event()

    async def fake_check_once(_engine: object) -> str:
        nonlocal call_count
        call_count += 1
        await release.wait()  # simulates a check that hasn't finished yet
        return "healthy"

    monkeypatch.setattr(db, "_check_once", fake_check_once)
    monkeypatch.setattr(db, "get_engine", lambda _settings: object())
    # Shrink the per-call timeout so this test doesn't need to wait on
    # the real 2-second production value.
    monkeypatch.setattr(db, "DATABASE_HEALTH_CHECK_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(db, "_pending_check", None)

    settings = _settings(database_app_password="x")

    async def scenario() -> None:
        # Three overlapping callers, all arriving while the one
        # underlying check is still stuck.
        results = await asyncio.gather(
            get_database_status(settings),
            get_database_status(settings),
            get_database_status(settings),
        )
        # asyncio.gather() returns a list at runtime, but mypy's stub
        # overloads infer a fixed-size tuple for this call shape - wrap
        # in list() so the comparison is correctly typed either way.
        assert list(results) == ["unreachable", "unreachable", "unreachable"]
        assert call_count == 1  # only one _check_once was ever started

        # Let the shared background check finish...
        release.set()
        await asyncio.sleep(0.01)

        # ...and confirm the system actually recovers: a fresh call now
        # starts a NEW check and reports the real (now-set) result,
        # rather than being stuck reporting "unreachable" forever.
        monkeypatch.setattr(db, "DATABASE_HEALTH_CHECK_TIMEOUT_SECONDS", 2.0)
        final = await get_database_status(settings)
        assert final == "healthy"
        assert call_count == 2  # a genuinely new check, not the stale one

    asyncio.run(scenario())


def test_a_completed_check_is_not_reused_by_the_next_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Once the shared task finishes, the next call must start a fresh
    check rather than replaying a stale cached result forever - this is
    what makes recovery after an outage actually work."""
    call_count = 0

    async def fake_check_once(_engine: object) -> str:
        nonlocal call_count
        call_count += 1
        return "unreachable" if call_count == 1 else "healthy"

    monkeypatch.setattr(db, "_check_once", fake_check_once)
    monkeypatch.setattr(db, "get_engine", lambda _settings: object())
    monkeypatch.setattr(db, "_pending_check", None)

    settings = _settings(database_app_password="x")

    async def scenario() -> None:
        first = await get_database_status(settings)
        second = await get_database_status(settings)
        assert first == "unreachable"
        assert second == "healthy"
        assert call_count == 2

    asyncio.run(scenario())
