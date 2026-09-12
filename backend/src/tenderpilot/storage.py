"""The object storage client and its one real I/O check.

Closes Chapter 5's remaining "connect the API to storage" work item
(docs/TECHNICAL_EXECUTION_PLAN.md Chapter 5, step 5) - connectivity and
readiness only, mirroring db.py's own shape closely on purpose: a lazily
created client, one bounded read-only check, merged into /health/ready.
No upload/download endpoints, no document pipeline, no bucket creation -
those are Chapter 10's job.

Uses the tenderpilot_app identity - the same restricted credential
verified in Chapter 5 Part B/round 3 (ADR 0006) - never the admin or
validator credential from that same identities file. That identity has
Read/Write/List on `quarantine` and Read/List (not Write) on `documents`;
this module only ever calls HeadBucket, which needs nothing more than
the ability to read the bucket at all.

Importing this module must never open a network connection or need
credentials - see get_client()'s laziness below, the same guarantee
db.py makes and for the same reason: `backend/scripts/export_openapi.py`
imports tenderpilot.main (which imports this module) with no .env, no
services running, and no network access, and must keep working exactly
as it does today.

Same shared in-flight check pattern as db.py, for the same reason: an
async caller awaiting a sync boto3 call via asyncio.to_thread() can
bound how long *it* waits, but cannot forcibly stop the underlying OS
thread - so at most one background check ever runs at a time here too,
via _pending_check, rather than letting overlapping/repeated polls pile
up threads behind a slow or stuck storage server.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Literal

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from tenderpilot.config import Settings

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client

logger = logging.getLogger("tenderpilot")

# Bounded on purpose, same reasoning as db.py's own timeout: a hung or
# unreachable storage server must not hang the readiness endpoint or
# anything waiting on it further out. Reused as the driver-level bound
# (connect_timeout/read_timeout) as well as the asyncio-level wait.
STORAGE_HEALTH_CHECK_TIMEOUT_SECONDS = 2.0

# A bucket the tenderpilot_app identity can definitely read (Read+List
# granted - see infra/compose/secrets/seaweedfs-s3-identities.json).
# HeadBucket only needs read access to the bucket itself, not to any
# object inside it, so this works whether or not the bucket is empty.
STORAGE_HEALTH_CHECK_BUCKET = "quarantine"

StorageStatus = Literal["not_configured", "healthy", "unreachable"]

_client: S3Client | None = None
_pending_check: asyncio.Task[StorageStatus] | None = None


def get_client(settings: Settings) -> S3Client | None:
    """Lazily creates one module-level boto3 S3 client and reuses it.
    None if no access key is configured - absence means "not set up
    yet", never "connect with an empty credential".

    path-style addressing (http://host:port/bucket/key), not the
    virtual-hosted style boto3 defaults to (http://bucket.host:port/key)
    - the verified, working configuration from Chapter 5 Part B/round 3:
    a bare IP/localhost endpoint has no DNS entry for a bucket-name
    subdomain, so virtual-hosted style silently fails (and, for
    presigned URLs specifically, breaks the signature itself, not just
    the address - see docs/technical/local-setup.md).

    Explicit, bounded timeouts and a small, bounded retry count -
    connect_timeout/read_timeout cover the two phases a single request
    can hang in, and max_attempts bounds retries so a persistently
    unreachable server fails within a known, short window rather than
    retrying indefinitely.
    """
    global _client
    if _client is None:
        if not settings.seaweedfs_app_access_key or not settings.seaweedfs_app_secret_key:
            return None
        _client = boto3.client(
            "s3",
            endpoint_url=settings.seaweedfs_endpoint_url,
            region_name="us-east-1",
            aws_access_key_id=settings.seaweedfs_app_access_key,
            aws_secret_access_key=settings.seaweedfs_app_secret_key,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=STORAGE_HEALTH_CHECK_TIMEOUT_SECONDS,
                read_timeout=STORAGE_HEALTH_CHECK_TIMEOUT_SECONDS,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )
    return _client


def _run_bounded_check(client: S3Client) -> None:
    """The actual blocking call - always invoked via asyncio.to_thread()
    from _check_once(), never directly on the event loop. HeadBucket is
    read-only: it proves the bucket exists and this credential can reach
    it, without listing, reading, or writing a single object."""
    client.head_bucket(Bucket=STORAGE_HEALTH_CHECK_BUCKET)


async def _check_once(client: S3Client) -> StorageStatus:
    """Runs the bounded check on a worker thread and reports the
    result. Never raises - a failure here becomes "unreachable", so
    that awaiters sharing this task (see get_storage_status) never see
    an exception propagate to them either."""
    try:
        await asyncio.to_thread(_run_bounded_check, client)
        return "healthy"
    except ClientError, BotoCoreError:
        logger.warning("storage_health_check_failed", exc_info=True)
        return "unreachable"


async def get_storage_status(settings: Settings) -> StorageStatus:
    """The only function in this codebase that does real object-storage
    I/O for a health check. Configuration presence alone is never
    reported as "healthy" - see config.get_dependency_status()'s
    docstring for why that distinction matters.

    If a previous check is still running when this is called, this does
    NOT start a second one - it awaits the same shared task, via
    asyncio.shield() so that THIS caller's own timeout can still give up
    and return "unreachable" on schedule without cancelling the shared
    background work out from under any other caller also waiting on it.
    """
    global _pending_check
    client = get_client(settings)
    if client is None:
        return "not_configured"

    if _pending_check is None or _pending_check.done():
        _pending_check = asyncio.create_task(_check_once(client))
    task = _pending_check

    try:
        async with asyncio.timeout(STORAGE_HEALTH_CHECK_TIMEOUT_SECONDS):
            return await asyncio.shield(task)
    except TimeoutError:
        return "unreachable"


async def dispose_client() -> None:
    """boto3's S3 client holds no persistent connection pool the way
    SQLAlchemy's engine does - closing it is synchronous and cheap, but
    kept as an async function and called from main.py's lifespan handler
    for symmetry with dispose_engine() and so this doesn't need to
    change if a future client type does need real async cleanup."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
