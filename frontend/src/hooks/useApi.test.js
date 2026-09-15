import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useApi } from "./useApi.js";

function deferred() {
  let resolve;
  const promise = new Promise((r) => (resolve = r));
  return { promise, resolve };
}

describe("useApi", () => {
  it("starts in a loading state and resolves to the fetched data", async () => {
    const fn = vi.fn().mockResolvedValue({ hello: "world" });
    const { result } = renderHook(() => useApi(fn, []));

    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual({ hello: "world" });
    expect(result.current.error).toBeNull();
  });

  it("surfaces a rejected fetch as `error`, not a thrown exception", async () => {
    const fn = vi.fn().mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useApi(fn, []));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error.message).toBe("boom");
    expect(result.current.data).toBeNull();
  });

  it("reports loading=true the instant deps change, before the new fetch resolves", async () => {
    // Regression test: a page that does `if (loading) return <Spinner/>` right
    // before reading `data.someField` must never see a stale `data` from the
    // *previous* deps once `loading` has flipped back to false — the classic
    // trigger is an id going from null -> a real value (e.g. picking a lot).
    const first = deferred();
    const second = deferred();
    const fn = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);

    const { result, rerender } = renderHook(({ id }) => useApi(fn, [id]), { initialProps: { id: null } });

    act(() => first.resolve({ value: "for-null-id" }));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual({ value: "for-null-id" });

    rerender({ id: "LOT_10901" });
    // Before the second promise resolves, loading must already be true again —
    // this is exactly the render where the buggy version still exposed the
    // first id's stale `data`.
    expect(result.current.loading).toBe(true);

    act(() => second.resolve({ value: "for-lot-10901" }));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual({ value: "for-lot-10901" });
  });
});
