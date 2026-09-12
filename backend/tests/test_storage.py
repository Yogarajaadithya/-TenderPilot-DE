"""Tests for storage.py's pure logic, its not_configured path, and the
shared-in-flight-check behavior that prevents accumulating threads.

Deliberately does NOT test the "healthy" branch against a real SeaweedFS
here - that's exercised manually against the actual Docker container
(see docs/technical/local-setup.md) and indirectly through
test_health.py's monkeypatched readiness tests. Keeping this suite free
of a real storage dependency is what lets it pass the same way in CI as
on a laptop with Docker running.
"""

from __future__ import annotations

import asyncio

import pytest

from tenderpilot import storage
from tenderpilot.config import Settings
from tenderpilot.storage import get_client, get_storage_status


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


def test_get_client_is_none_without_an_access_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "_client", None)
    settings = _settings(seaweedfs_app_access_key=None, seaweedfs_app_secret_key=None)
    assert get_client(settings) is None


def test_get_client_is_none_without_a_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "_client", None)
    settings = _settings(seaweedfs_app_access_key="an-access-key", seaweedfs_app_secret_key=None)
    assert get_client(settings) is None


def test_get_client_uses_path_style_addressing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression test for the exact bug found and fixed in
    # infra/compose/scripts/verify_seaweedfs.py: boto3 defaults to
    # virtual-hosted-style addressing, which silently fails against a
    # bare-IP/localhost endpoint with no DNS entry for a bucket-name
    # subdomain.
    monkeypatch.setattr(storage, "_client", None)
    settings = _settings(
        seaweedfs_app_access_key="an-access-key",
        seaweedfs_app_secret_key="a-secret-key",
    )
    client = get_client(settings)
    assert client is not None
    assert client.meta.config.s3["addressing_style"] == "path"  # type: ignore[attr-defined]


def test_get_storage_status_is_not_configured_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(storage, "_client", None)
    settings = _settings(seaweedfs_app_access_key=None, seaweedfs_app_secret_key=None)
    result = asyncio.run(get_storage_status(settings))
    assert result == "not_configured"


def test_overlapping_calls_share_one_in_flight_check_instead_of_starting_a_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for the same thread-accumulation review finding
    already fixed in db.py: asyncio.timeout() around asyncio.to_thread()
    does not stop an already-running thread, so a naive implementation
    could start a new one on every call while a previous, still-hanging
    check lingers in the background. This proves at most ONE underlying
    check ever runs at a time, no matter how many overlapping callers
    arrive - and that the system still recovers once that check
    actually finishes."""
    call_count = 0
    release = asyncio.Event()

    async def fake_check_once(_client: object) -> str:
        nonlocal call_count
        call_count += 1
        await release.wait()  # simulates a check that hasn't finished yet
        return "healthy"

    monkeypatch.setattr(storage, "_check_once", fake_check_once)
    monkeypatch.setattr(storage, "get_client", lambda _settings: object())
    # Shrink the per-call timeout so this test doesn't need to wait on
    # the real 2-second production value.
    monkeypatch.setattr(storage, "STORAGE_HEALTH_CHECK_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(storage, "_pending_check", None)

    settings = _settings(seaweedfs_app_access_key="x", seaweedfs_app_secret_key="y")

    async def scenario() -> None:
        # Three overlapping callers, all arriving while the one
        # underlying check is still stuck.
        results = await asyncio.gather(
            get_storage_status(settings),
            get_storage_status(settings),
            get_storage_status(settings),
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
        monkeypatch.setattr(storage, "STORAGE_HEALTH_CHECK_TIMEOUT_SECONDS", 2.0)
        final = await get_storage_status(settings)
        assert final == "healthy"
        assert call_count == 2  # a genuinely new check, not the stale one

    asyncio.run(scenario())


def test_a_completed_check_is_not_reused_by_the_next_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Once the shared task finishes, the next call must start a fresh
    check rather than replaying a stale cached result forever - this is
    what makes recovery after an outage actually work."""
    call_count = 0

    async def fake_check_once(_client: object) -> str:
        nonlocal call_count
        call_count += 1
        return "unreachable" if call_count == 1 else "healthy"

    monkeypatch.setattr(storage, "_check_once", fake_check_once)
    monkeypatch.setattr(storage, "get_client", lambda _settings: object())
    monkeypatch.setattr(storage, "_pending_check", None)

    settings = _settings(seaweedfs_app_access_key="x", seaweedfs_app_secret_key="y")

    async def scenario() -> None:
        first = await get_storage_status(settings)
        second = await get_storage_status(settings)
        assert first == "unreachable"
        assert second == "healthy"
        assert call_count == 2

    asyncio.run(scenario())
