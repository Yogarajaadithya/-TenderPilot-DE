import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createHealthPoller, type HealthCheckResult } from "./health-poller";

describe("createHealthPoller", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("checks immediately on start, then again every intervalMs", async () => {
    const results: HealthCheckResult[] = [];
    const fetcher = vi.fn(
      async (): Promise<HealthCheckResult> => ({ status: "connected", backend: {} }),
    );

    const poller = createHealthPoller({
      intervalMs: 5000,
      fetcher,
      onResult: (r) => results.push(r),
    });

    poller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(fetcher).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(5000);
    expect(fetcher).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(5000);
    expect(fetcher).toHaveBeenCalledTimes(3);

    poller.stop();
  });

  it("detects an outage then recovery across successive polls, without any page reload", async () => {
    const results: HealthCheckResult[] = [];
    let up = true;
    const fetcher = vi.fn(async (): Promise<HealthCheckResult> => {
      if (up) return { status: "connected", backend: { ok: true } };
      throw new Error("ECONNREFUSED");
    });

    const poller = createHealthPoller({
      intervalMs: 5000,
      fetcher,
      onResult: (r) => results.push(r),
    });

    poller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(results.at(-1)?.status).toBe("connected");

    up = false;
    await vi.advanceTimersByTimeAsync(5000);
    expect(results.at(-1)?.status).toBe("unavailable");

    up = true;
    await vi.advanceTimersByTimeAsync(5000);
    expect(results.at(-1)?.status).toBe("connected");

    poller.stop();
  });

  it("does not overlap requests - a slow check blocks the next tick until it resolves", async () => {
    let resolveFirst!: (r: HealthCheckResult) => void;
    let callCount = 0;
    const fetcher = vi.fn((): Promise<HealthCheckResult> => {
      callCount += 1;
      if (callCount === 1) {
        return new Promise((resolve) => {
          resolveFirst = resolve;
        });
      }
      return Promise.resolve({ status: "connected", backend: {} });
    });

    const poller = createHealthPoller({ intervalMs: 5000, fetcher, onResult: () => {} });
    poller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(fetcher).toHaveBeenCalledTimes(1);

    // A full interval passes while the first check is still in flight -
    // the overlap guard must skip this tick rather than starting a second
    // concurrent request.
    await vi.advanceTimersByTimeAsync(5000);
    expect(fetcher).toHaveBeenCalledTimes(1);

    resolveFirst({ status: "connected", backend: {} });
    await vi.advanceTimersByTimeAsync(0);

    await vi.advanceTimersByTimeAsync(5000);
    expect(fetcher).toHaveBeenCalledTimes(2);

    poller.stop();
  });

  it("reports unavailable when a check exceeds timeoutMs, via the abort signal", async () => {
    const results: HealthCheckResult[] = [];
    const fetcher = vi.fn((signal: AbortSignal): Promise<HealthCheckResult> => {
      return new Promise((_resolve, reject) => {
        signal.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
      });
    });

    const poller = createHealthPoller({
      intervalMs: 10000,
      timeoutMs: 4000,
      fetcher,
      onResult: (r) => results.push(r),
    });

    poller.start();
    await vi.advanceTimersByTimeAsync(4000);

    expect(results).toHaveLength(1);
    expect(results[0]).toEqual({
      status: "unavailable",
      message: "Health check timed out after 4000ms.",
    });

    poller.stop();
  });

  it("stops calling onResult once stopped, even if an in-flight check resolves later", async () => {
    let resolveCheck!: (r: HealthCheckResult) => void;
    const fetcher = vi.fn(
      () =>
        new Promise<HealthCheckResult>((resolve) => {
          resolveCheck = resolve;
        }),
    );
    const onResult = vi.fn();

    const poller = createHealthPoller({ intervalMs: 5000, fetcher, onResult });
    poller.start();
    await vi.advanceTimersByTimeAsync(0);

    poller.stop();
    resolveCheck({ status: "connected", backend: {} });
    await vi.advanceTimersByTimeAsync(0);

    expect(onResult).not.toHaveBeenCalled();
  });

  it("stops scheduling further ticks after stop() (cleanup)", async () => {
    const fetcher = vi.fn(async (): Promise<HealthCheckResult> => ({ status: "connected", backend: {} }));
    const poller = createHealthPoller({ intervalMs: 5000, fetcher, onResult: () => {} });

    poller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(fetcher).toHaveBeenCalledTimes(1);

    poller.stop();
    await vi.advanceTimersByTimeAsync(20000);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("defaults timeoutMs above route.ts's own 5000ms backend-fetch timeout, so it never gives up first", async () => {
    // Regression test for the timeout-ordering bug: this used to default
    // to 4000ms, which is LESS than the 5000ms the route gives the
    // backend to respond - meaning the poller could report a generic
    // "timed out" before the route had even finished producing its own,
    // more specific answer.
    const results: HealthCheckResult[] = [];
    const fetcher = vi.fn(
      (signal: AbortSignal) =>
        new Promise<HealthCheckResult>((_resolve, reject) => {
          signal.addEventListener("abort", () =>
            reject(Object.assign(new Error("aborted"), { name: "AbortError" })),
          );
        }),
    );

    // No timeoutMs override here - this is asserting the DEFAULT.
    const poller = createHealthPoller({ intervalMs: 10000, fetcher, onResult: (r) => results.push(r) });
    poller.start();

    await vi.advanceTimersByTimeAsync(5000);
    expect(results).toHaveLength(0); // route.ts's own 5000ms window hasn't been exceeded yet

    await vi.advanceTimersByTimeAsync(1000); // now at 6000ms, the default timeoutMs
    expect(results).toHaveLength(1);
    expect(results[0]).toEqual({
      status: "unavailable",
      message: "Health check timed out after 6000ms.",
    });

    poller.stop();
  });
});
