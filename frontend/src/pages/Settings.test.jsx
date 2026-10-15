import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Settings from "./Settings.jsx";
import { get } from "../services/api";

vi.mock("../services/api", () => ({
  get: vi.fn(),
}));

const OPS_PAYLOAD = {
  window_spans: 3,
  endpoints: [
    { count: 2, max_ms: 45.2, name: "GET /api/dashboard", p50_ms: 30.1, p95_ms: 45.2, avg_ms: 30.1, active_count: 0 },
    { count: 1, max_ms: 120.5, name: "POST /api/yield/predict", p50_ms: 120.5, p95_ms: 120.5, avg_ms: 120.5, active_count: 0 },
  ],
  counters: { "requests:GET /api/dashboard": 2, "requests:GET /api/dashboard:errors": 0 },
};

function setRole(role) {
  // Settings reads auth state from localStorage, like the real page does.
  if (role) {
    localStorage.setItem("wafguard_username", "admin");
    localStorage.setItem("wafguard_role", role);
    localStorage.setItem("wafguard_token", "fake-token");
  } else {
    localStorage.clear();
  }
}

describe("Settings — live ops panel (Phase 7)", () => {
  beforeEach(() => {
    localStorage.clear();
  });
  afterEach(() => {
    vi.resetAllMocks();
    localStorage.clear();
  });

  it("does not fetch ops metrics for non-admin roles", () => {
    setRole("viewer");
    render(<Settings />);
    expect(get).not.toHaveBeenCalled();
    expect(screen.queryByText(/Live Ops/)).not.toBeInTheDocument();
  });

  it("does not fetch ops metrics when signed out", () => {
    setRole(null);
    render(<Settings />);
    expect(get).not.toHaveBeenCalled();
  });

  it("renders the per-endpoint latency table for admins", async () => {
    setRole("admin");
    get.mockResolvedValue(OPS_PAYLOAD);
    render(<Settings />);

    await waitFor(() => expect(screen.getByText("GET /api/dashboard")).toBeInTheDocument());
    expect(screen.getByText("POST /api/yield/predict")).toBeInTheDocument();
    // p95 == max for row 1 (45.2 -> "45 ms" both), so assert multiplicty: 45.2 ->
    expect(screen.getAllByText("45 ms").length).toBe(2); // p95 and max rounded at render
    expect(screen.getByText("3 span(s) recorded in the current window.")).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith("/ops/metrics", { window: 100 });
  });

  it("shows a friendly error when the ops API fails", async () => {
    setRole("admin");
    get.mockRejectedValue(new Error("Forbidden"));
    render(<Settings />);

    await waitFor(() => expect(screen.getByText(/Ops metrics unavailable/i)).toBeInTheDocument());
    expect(screen.queryByText("GET /api/dashboard")).not.toBeInTheDocument();
  });

  it("shows the empty-window state for a fresh API process", async () => {
    setRole("admin");
    get.mockResolvedValue({ window_spans: 0, endpoints: [], counters: {} });
    render(<Settings />);

    await waitFor(() => expect(screen.getByText(/No traffic recorded yet/i)).toBeInTheDocument());
  });
});
