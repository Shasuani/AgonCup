// Tiny request-state helpers (deliberately NOT react-query — no extra dep).
//
// `useAsyncData` runs a fetcher on mount/dep-change and tracks loading/error/
// data; `useAsyncAction` wraps an imperative call (e.g. a mutation on submit)
// and tracks its in-flight + error state. Both surface ApiError.detail-friendly
// errors via the raw Error so callers can render with isApiError().

import { useCallback, useEffect, useRef, useState } from "react";

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: Error | null;
}

export interface AsyncDataResult<T> extends AsyncState<T> {
  /** Re-run the fetcher imperatively (e.g. a refresh button). */
  reload: () => void;
}

// Re-export React hooks under stable names so this stays self-contained.
const useCallbackHook = useCallback;

/**
 * Run an async `fetcher` and expose {data, loading, error, reload}. Re-runs
 * whenever `deps` change. Stale responses (from superseded runs) are ignored.
 */
export function useAsyncData<T>(
  fetcher: () => Promise<T>,
  deps: ReadonlyArray<unknown> = [],
): AsyncDataResult<T> {
  const [state, setState] = useState<AsyncState<T>>({
    data: null,
    loading: true,
    error: null,
  });

  // Monotonic run id: only the latest run is allowed to commit state.
  const runIdRef = useRef(0);

  const run = useCallbackHook(() => {
    const runId = ++runIdRef.current;
    setState((prev) => ({ ...prev, loading: true, error: null }));
    fetcher()
      .then((data) => {
        if (runIdRef.current === runId) {
          setState({ data, loading: false, error: null });
        }
      })
      .catch((err: unknown) => {
        if (runIdRef.current === runId) {
          setState({ data: null, loading: false, error: toError(err) });
        }
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    run();
    return () => {
      // Invalidate any in-flight run on unmount / dep change.
      runIdRef.current++;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { ...state, reload: run };
}

export interface AsyncActionResult<Args extends unknown[], T> {
  /** Invoke the action; resolves with the value or rejects with the error. */
  run: (...args: Args) => Promise<T>;
  loading: boolean;
  error: Error | null;
  /** Last successful result, if any. */
  data: T | null;
  /** Clear the error/data state. */
  reset: () => void;
}

/** Wrap an imperative async action (mutation) with in-flight + error state. */
export function useAsyncAction<Args extends unknown[], T>(
  action: (...args: Args) => Promise<T>,
): AsyncActionResult<Args, T> {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [data, setData] = useState<T | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const run = useCallback(
    async (...args: Args): Promise<T> => {
      setLoading(true);
      setError(null);
      try {
        const result = await action(...args);
        if (mountedRef.current) {
          setData(result);
          setLoading(false);
        }
        return result;
      } catch (err: unknown) {
        const e = toError(err);
        if (mountedRef.current) {
          setError(e);
          setLoading(false);
        }
        throw e;
      }
    },
    [action],
  );

  const reset = useCallback(() => {
    setError(null);
    setData(null);
  }, []);

  return { run, loading, error, data, reset };
}

/** Normalize any thrown value into an Error (preserves ApiError instances). */
export function toError(err: unknown): Error {
  if (err instanceof Error) return err;
  return new Error(typeof err === "string" ? err : "Unknown error");
}
