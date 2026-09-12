"""The database engine and its one real I/O check.

Chapter 5 Part A: connectivity and readiness only - no ORM models, no
migrations, no business schema yet (Chapter 6 introduces those). This
module is deliberately small: building the connection URL, a lazily
created engine, and one bounded query used by /health/ready.

Importing this module must never open a network connection or need
credentials - see get_engine()'s laziness below. That's what lets
`backend/scripts/export_openapi.py` keep working with no .env, no
database, and no network access: it imports tenderpilot.main, which
imports this module, but never calls get_database_status().

Sync engine, run through a worker thread - not create_async_engine().
This was tried first and hit a real, verified platform limitation:
psycopg 3's ASYNC mode cannot run under Windows' default asyncio event
loop (ProactorEventLoop) - only a selector-based loop, which isn't the
default on Windows. Rather than changing the whole app's event loop
policy (a global change with its own tradeoffs, e.g. for future
subprocess-based job workers), the actual database call here runs
synchronously on a worker thread via asyncio.to_thread() - psycopg 3's
SYNC driver has no such restriction.

A real limitation of that approach, found in review: asyncio.timeout()
wrapped around an awaited asyncio.to_thread() bounds how long the
CALLER waits, but it does NOT stop the underlying OS thread - Python
cannot forcibly kill a running thread. If every /health/ready call
started a fresh thread, a genuinely stuck connection could leave
threads piling up behind it, one per poll, for as long as the database
stays stuck. Two things narrow this a lot, though NOT completely - see
the caveat below:
  1. Every phase of the work is bounded at the driver/pool level, not
     just from the asyncio side - see pool_timeout, connect_timeout,
     and statement_timeout below.
  2. At most ONE such background check ever runs at a time - see
     _pending_check below. A caller who arrives while a check is
     already in flight reuses that same shared task instead of
     starting a second thread, so overlapping or repeated polls can
     never multiply the number of in-flight (or abandoned) threads.

CAVEAT, corrected from an earlier overclaim: statement_timeout is a
SERVER-side setting - it bounds how long PostgreSQL keeps *executing*
the query, and it makes the server attempt to send an error back. It
does NOT bound how long the CLIENT's underlying socket read() waits to
actually *receive* that response. If the network connection itself
goes truly dead after the query is sent (packets silently dropped, no
RST/FIN ever arrives - not the same as the server refusing or closing
the connection, which fails fast), the worker thread's blocking read()
has no timeout of its own and could still hang past statement_timeout,
because the server's response never gets there either. Closing this
fully needs a client-side guard psycopg/libpq expose (e.g. TCP
keepalives, or a socket-level read timeout) that this module does not
yet set - tracked as real, open work for the reliability gate (Ch19),
not fixed here. What IS true: this module's own asyncio.timeout() still
bounds what any *caller* waits for either way, and the pool/connect/
statement timeouts remove the far more common failure modes (a slow
pool, a slow connect, a slow-but-responsive query) - the gap is
specifically a dead-network edge case, not the everyday timeout path.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from sqlalchemy import URL, create_engine, text
from sqlalchemy.engine import Engine

from tenderpilot.config import Settings

logger = logging.getLogger("tenderpilot")

# Bounded on purpose: a hung or unreachable database must not hang the
# readiness endpoint, or anything waiting on it further out (see the
# timeout-ordering note in main.py's readiness endpoint). Reused as the
# driver-level bound for every phase of the work (pool checkout,
# connect, and the query itself), not just the asyncio-level wait.
DATABASE_HEALTH_CHECK_TIMEOUT_SECONDS = 2.0

DatabaseStatus = Literal["not_configured", "healthy", "unreachable"]

_engine: Engine | None = None
_pending_check: asyncio.Task[DatabaseStatus] | None = None


def build_database_url(settings: Settings) -> URL | None:
    """None if no runtime-role password is configured - absence means
    "not set up yet", never "connect with an empty password".

    Uses SQLAlchemy's URL.create() rather than interpolating the
    password into an f-string. A password containing a character that's
    special in a URL (@, :, /, %, #, ...) would silently corrupt an
    f-string-built URL - e.g. an "@" in the password would be parsed as
    the start of the host section, connecting with the wrong username
    entirely rather than raising a clear error. URL.create() percent-
    encodes each component correctly, and str(URL) hides the password
    by default (call .render_as_string(hide_password=False) if the
    literal value is ever needed), so an accidental log/print of this
    value doesn't leak the credential either.
    """
    if not settings.database_app_password:
        return None
    return URL.create(
        drivername="postgresql+psycopg",
        username=settings.database_app_user,
        password=settings.database_app_password,
        host=settings.database_host,
        port=settings.database_port,
        database=settings.database_name,
    )


def get_engine(settings: Settings) -> Engine | None:
    """Lazily creates one module-level engine (a connection pool
    descriptor, not a connection) and reuses it across requests.
    create_engine() itself performs no I/O - the pool only opens a real
    connection when something actually asks it to.

    Three separate bounds on the work this engine can do, each covering
    a different phase - a timeout on one phase does not cover another:
      - pool_timeout: how long to wait for a free connection slot out of
        the pool (pool_size=2, max_overflow=0 - so a 3rd concurrent
        checkout would otherwise wait on SQLAlchemy's own default of
        30 seconds).
      - connect_args.connect_timeout: libpq's bound on the TCP connect +
        authentication handshake.
      - connect_args.options "-c statement_timeout=...": PostgreSQL's
        own SERVER-side bound on how long a query is allowed to run
        once a connection is already established - connect_timeout does
        NOT cover this phase, a fast-connecting-but-then-hanging server
        would otherwise not be caught by it at all. Server-side only,
        though: it bounds the server's execution and makes it attempt
        to respond, but does not itself bound the client's wait for
        that response to actually arrive over a truly dead connection -
        see the module docstring's caveat above.
    """
    global _engine
    if _engine is None:
        url = build_database_url(settings)
        if url is None:
            return None
        timeout_seconds = int(DATABASE_HEALTH_CHECK_TIMEOUT_SECONDS)
        timeout_ms = int(DATABASE_HEALTH_CHECK_TIMEOUT_SECONDS * 1000)
        _engine = create_engine(
            url,
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=0,
            pool_timeout=timeout_seconds,
            connect_args={
                "connect_timeout": timeout_seconds,
                "options": f"-c statement_timeout={timeout_ms}",
            },
        )
    return _engine


def _run_bounded_query(engine: Engine) -> None:
    """The actual blocking call - always invoked via asyncio.to_thread()
    from _check_once(), never directly on the event loop."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


async def _check_once(engine: Engine) -> DatabaseStatus:
    """Runs the bounded query on a worker thread and reports the
    result. Never raises - a failure here becomes "unreachable", so
    that awaiters sharing this task (see get_database_status) never see
    an exception propagate to them either."""
    try:
        await asyncio.to_thread(_run_bounded_query, engine)
        return "healthy"
    except Exception:
        logger.warning("database_health_check_failed", exc_info=True)
        return "unreachable"


async def get_database_status(settings: Settings) -> DatabaseStatus:
    """The only function in this codebase that does real database I/O
    for a health check.

    If a previous check is still running when this is called, this does
    NOT start a second one - it awaits the same shared task, via
    asyncio.shield() so that THIS caller's own timeout can still give up
    and return "unreachable" on schedule without cancelling the shared
    background work out from under any other caller also waiting on it.
    That shared task keeps running until it actually finishes (bounded,
    per get_engine(), by the driver/pool timeouts above) - the next
    call after it completes starts a fresh one.
    """
    global _pending_check
    engine = get_engine(settings)
    if engine is None:
        return "not_configured"

    if _pending_check is None or _pending_check.done():
        _pending_check = asyncio.create_task(_check_once(engine))
    task = _pending_check

    try:
        async with asyncio.timeout(DATABASE_HEALTH_CHECK_TIMEOUT_SECONDS):
            return await asyncio.shield(task)
    except TimeoutError:
        return "unreachable"


async def dispose_engine() -> None:
    """Releases the pool's connections on app shutdown - see main.py's
    lifespan handler. Safe to call even if the engine was never created."""
    global _engine
    if _engine is not None:
        await asyncio.to_thread(_engine.dispose)
        _engine = None
