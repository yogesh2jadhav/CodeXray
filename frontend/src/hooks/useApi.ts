/**
 * src/hooks/useApi.ts
 *
 * Purpose:        Tiny data-fetching helpers so pages stay declarative.
 * Responsibility:
 *   - `useAsync(fn, deps)` runs an async fn on mount/deps-change, exposing
 *     { data, error, loading, reload }.
 *   - `useAction()` wraps a one-shot mutation with { run, data, error, pending }.
 * Notes: no external state library; just useState/useEffect.
 */
import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../api/client";

interface AsyncState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    fn()
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(e instanceof ApiError ? e.message : String(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}

export function useAction<A extends unknown[], T>(fn: (...args: A) => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const run = useCallback(
    async (...args: A) => {
      setPending(true);
      setError(null);
      try {
        const d = await fn(...args);
        setData(d);
        return d;
      } catch (e) {
        setError(e instanceof ApiError ? e.message : String(e));
        throw e;
      } finally {
        setPending(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  return { run, data, error, pending, setData };
}
