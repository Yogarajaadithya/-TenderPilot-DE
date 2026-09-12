"use client";

import { useEffect, useState } from "react";
import { createHealthPoller, type HealthCheckResult } from "./health-poller";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

async function fetchHealth(signal: AbortSignal): Promise<HealthCheckResult> {
  const response = await fetch("/api/health", { cache: "no-store", signal });
  const body: unknown = await response.json();

  if (response.ok && isRecord(body) && body.status === "connected") {
    return { status: "connected", backend: body.backend };
  }
  if (isRecord(body) && typeof body.message === "string") {
    return { status: "unavailable", message: body.message };
  }
  return { status: "unavailable", message: "Unexpected response from the server." };
}

/**
 * Polls /api/health every `intervalMs` (default 5s) and returns the most
 * recent result. Returns null only before the very first check resolves -
 * after that, the last known result stays visible while a new check runs
 * in the background, so the screen never flashes back to a loading state
 * on every poll.
 */
export function useBackendHealth(intervalMs = 5000): HealthCheckResult | null {
  const [result, setResult] = useState<HealthCheckResult | null>(null);

  useEffect(() => {
    const poller = createHealthPoller({
      intervalMs,
      fetcher: fetchHealth,
      onResult: setResult,
    });
    poller.start();
    return () => poller.stop();
  }, [intervalMs]);

  return result;
}
