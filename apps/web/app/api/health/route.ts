import type { components } from "@/lib/api-types.generated";

/**
 * Server-side proxy to the FastAPI backend's readiness endpoint.
 *
 * The browser never talks to FastAPI directly - it only ever calls this
 * same-origin Next.js route. BACKEND_URL (the backend's real address)
 * lives only in server-side env config and never reaches the client
 * bundle, since it has no NEXT_PUBLIC_ prefix.
 *
 * Bounded timeout: a hung backend must not hang this route forever.
 * No caching: a health check that returns cached data is not a health
 * check.
 *
 * Runtime validation: the generated ReadinessResponse type only tells
 * TypeScript what the shape *should* be at compile time - it says
 * nothing about what actually arrives over the wire at runtime. A
 * response could be truncated, a proxy could return an HTML error page
 * with a 200 status, or the backend could be running an older/newer
 * contract version than the one types were generated from. isReadinessResponse()
 * checks the parsed JSON's actual shape before this route is willing to
 * report "connected" - the type declaration alone would compile fine
 * against nonsense and label it connected anyway.
 */

const REQUEST_TIMEOUT_MS = 5000;

export const dynamic = "force-dynamic"; // never statically cache this route

type ReadinessResponse = components["schemas"]["ReadinessResponse"];

function isReadinessResponse(value: unknown): value is ReadinessResponse {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    (candidate.status === "ready" || candidate.status === "degraded") &&
    typeof candidate.app_env === "string" &&
    // `typeof x === "object"` is true for arrays too, in JavaScript -
    // without excluding arrays explicitly here, a body like
    // `"dependencies": ["not_configured", "healthy"]` would pass this
    // check (Object.values() on an array just returns its elements),
    // even though it isn't the key/value map this route promises.
    typeof candidate.dependencies === "object" &&
    candidate.dependencies !== null &&
    !Array.isArray(candidate.dependencies) &&
    Object.values(candidate.dependencies as Record<string, unknown>).every(
      (v) => typeof v === "string",
    )
  );
}

export async function GET(): Promise<Response> {
  const backendUrl = process.env.BACKEND_URL;

  if (!backendUrl) {
    return Response.json(
      {
        status: "misconfigured",
        message: "BACKEND_URL is not set in the server environment.",
      },
      { status: 500 },
    );
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const backendResponse = await fetch(`${backendUrl}/health/ready`, {
      signal: controller.signal,
      cache: "no-store",
    });

    if (!backendResponse.ok) {
      return Response.json(
        {
          status: "unavailable",
          message: `Backend responded with HTTP ${backendResponse.status}.`,
        },
        { status: 502 },
      );
    }

    let backendBody: unknown;
    try {
      backendBody = await backendResponse.json();
    } catch {
      return Response.json(
        {
          status: "unavailable",
          message: "Backend response was not valid JSON.",
        },
        { status: 502 },
      );
    }

    if (!isReadinessResponse(backendBody)) {
      return Response.json(
        {
          status: "unavailable",
          message: "Backend response did not match the expected readiness contract.",
        },
        { status: 502 },
      );
    }

    return Response.json(
      { status: "connected", backend: backendBody },
      { status: 200 },
    );
  } catch (error) {
    const isTimeout = error instanceof Error && error.name === "AbortError";
    return Response.json(
      {
        status: "unavailable",
        message: isTimeout
          ? `Backend did not respond within ${REQUEST_TIMEOUT_MS}ms.`
          : "Could not reach the backend.",
      },
      { status: 502 },
    );
  } finally {
    clearTimeout(timeout);
  }
}
