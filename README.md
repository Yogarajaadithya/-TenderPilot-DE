# TenderPilot DE

A private, explainable qualification and evidence workspace for German companies bidding on public-sector tenders. It extracts requirements with citations, matches them against a company's own approved evidence, surfaces gaps and uncertainty, and lets a human bid manager record a traceable bid/no-bid decision — it is deliberately **not** a tender-search engine; it starts where discovery tools stop.

This repository is a **code-only mirror** of an actively developed private project. It contains the application source (backend + frontend) and CI configuration only — no infrastructure definitions, planning documents, or internal decision logs, which live in the private working repository.

## Status

Foundation chapters are complete and verified:

- A FastAPI backend with validated configuration, structured JSON logging, per-request IDs, typed error handling, and liveness/readiness health checks.
- A Next.js/TypeScript frontend with a same-origin server route as the only caller of the backend, response-shape validation, and a resilient health-polling hook.
- Generated OpenAPI types shared between backend and frontend, with an automated contract-drift check.
- A real, bounded database connectivity check through a restricted, least-privilege PostgreSQL role (no superuser, no RLS-bypass).
- A real, read-only object-storage connectivity check (S3-compatible) through a restricted application credential, with explicit timeouts and bounded retries.

Local infrastructure (PostgreSQL/pgvector, an S3-compatible object store, RabbitMQ, Keycloak) is defined and verified in the private repository's Docker Compose setup, which isn't published here. The next milestone is the initial database schema, migrations, and tenant-isolation tests.

## Tech stack

| Layer | Choice |
|---|---|
| Web | Next.js, React, TypeScript |
| API | FastAPI, Pydantic, SQLAlchemy, Alembic |
| Database | PostgreSQL + pgvector |
| Object storage | S3-compatible (private, restricted credentials) |
| Identity | OIDC (Keycloak in local development) |
| Jobs | Celery + RabbitMQ (planned; broker verified, job workflow not yet built) |
| Testing | pytest, Playwright |
| Delivery | Docker Compose, GitHub Actions |

## Repository layout

```
backend/               FastAPI application (uv-managed)
  src/tenderpilot/         application package
  tests/                    pytest suite
  scripts/export_openapi.py   exports the OpenAPI schema without a live server
apps/web/              Next.js application (npm-managed)
  app/                      pages and the same-origin API proxy route
  lib/                      health-polling and typed API client code
.github/workflows/     CI: backend lint/type-check/test, frontend lint/type-check/test/build, contract-drift check
```

## Running the backend

```bash
cd backend
uv sync --locked
uv run pytest
uv run ruff check .
uv run mypy .
```

The backend's readiness endpoint performs real checks against PostgreSQL and object storage; without those services running (see the private repository's Compose setup) it reports `not_configured`/`unreachable` honestly rather than a false "healthy" — no test or lint command above requires them.

## Running the frontend

```bash
cd apps/web
npm ci
npm run lint
npm run typecheck
npm test
npm run build
```

## Product invariants

A few rules the codebase is built around, regardless of feature:

- Every private record belongs to exactly one organization; a client-supplied tenant ID is never sufficient authorization on its own.
- Configuration presence is never reported as connectivity — a health check only says "healthy" after a real, bounded I/O call succeeds.
- Unknown, absent, contradictory, expired, and unsatisfied evidence are distinct states, never collapsed into one score.
- An AI-generated recommendation never becomes a human decision automatically.

---

This is a solo-built portfolio project targeting AI-engineering roles in the German market. It is under active development; the private repository holds the full planning documents, architecture decisions, and infrastructure definitions.
