import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Fetches `fn` (an async function returning unwrapped data, e.g. () => get("/dashboard"))
 * whenever `deps` change. Returns {data, loading, error, reload}.
 *
 * `loading` also goes true the instant `deps` changes, computed synchronously
 * from a ref during render — not just from the internal `loading` state.
 * Without that, there's a render pass where React has already applied the
 * new deps (e.g. a new lotId from the URL) but the effect that re-fetches
 * for them hasn't run yet, so `loading`/`data` are still whatever the
 * *previous* deps left behind. A page that reads `data.candidates` as soon
 * as `!loading` is true crashes on that stale null in that one render.
 */
export function useApi(fn, deps = []) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const depsKey = JSON.stringify(deps);
  const fetchedForKeyRef = useRef(null);
  const isStale = fetchedForKeyRef.current !== depsKey;

  const load = useCallback(() => {
    fetchedForKeyRef.current = depsKey;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fnRef
      .current()
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((err) => {
        if (!cancelled) setError(err);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [depsKey]);

  useEffect(() => load(), [load]);

  return { data, loading: loading || isStale, error, reload: load };
}
