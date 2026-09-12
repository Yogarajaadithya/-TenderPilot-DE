/**
 * A plain (non-React) polling loop for the backend connectivity check.
 *
 * Deliberately framework-free so the outage-detection logic - overlap
 * prevention, timeout enforcement, cleanup - can be unit-tested directly
 * with fake timers, without needing a DOM or a React renderer. A thin
 * hook (useBackendHealth.ts) wraps this for the page component.
 */

export type HealthCheckResult =
  | { status: "connected"; backend: unknown }
  | { status: "unavailable"; message: string };

export type HealthFetcher = (signal: AbortSignal) => Promise<HealthCheckResult>;

export interface HealthPollerOptions {
  /** How often to re-check once running. Default 5000ms. */
  intervalMs?: number;
  /**
   * How long a single check may take before it's treated as a timeout.
   * Default 6000ms - deliberately ABOVE app/api/health/route.ts's own
   * 5000ms fetch-to-backend timeout. If this were shorter than that
   * (it used to default to 4000ms), the poller could give up and report
   * a generic "timed out" before the route had even finished producing
   * its own real, more specific answer. Each outer layer's timeout
   * should exceed the layer it calls, with headroom - see the ordering
   * note in main.py's readiness endpoint for the same rule applied one
   * layer further in (database -> route -> poller).
   */
  timeoutMs?: number;
  fetcher: HealthFetcher;
  onResult: (result: HealthCheckResult) => void;
}

export interface HealthPoller {
  start: () => void;
  stop: () => void;
}

export function createHealthPoller({
  intervalMs = 5000,
  timeoutMs = 6000,
  fetcher,
  onResult,
}: HealthPollerOptions): HealthPoller {
  let intervalTimer: ReturnType<typeof setInterval> | null = null;
  let controller: AbortController | null = null;
  let stopped = false;

  async function tick(): Promise<void> {
    // A check is already in flight - skip this tick rather than piling up
    // overlapping requests. The next scheduled tick will try again.
    if (controller || stopped) return;

    controller = new AbortController();
    const abortTimer = setTimeout(() => controller?.abort(), timeoutMs);

    try {
      const result = await fetcher(controller.signal);
      if (!stopped) onResult(result);
    } catch (error) {
      if (!stopped) {
        const isTimeout = error instanceof Error && error.name === "AbortError";
        onResult({
          status: "unavailable",
          message: isTimeout
            ? `Health check timed out after ${timeoutMs}ms.`
            : "Could not reach the server.",
        });
      }
    } finally {
      clearTimeout(abortTimer);
      controller = null;
    }
  }

  function start(): void {
    stopped = false;
    void tick(); // check immediately, don't wait for the first interval
    intervalTimer = setInterval(() => void tick(), intervalMs);
  }

  function stop(): void {
    stopped = true;
    if (intervalTimer) clearInterval(intervalTimer);
    intervalTimer = null;
    controller?.abort();
    controller = null;
  }

  return { start, stop };
}
