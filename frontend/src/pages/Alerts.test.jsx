import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Alerts from "./Alerts.jsx";
import { get, post } from "../services/api";

vi.mock("../services/api", () => ({
  get: vi.fn(),
  post: vi.fn(),
}));

const OPEN = {
  alert_id: "a1",
  lot_id: "LOT_1",
  severity: "HIGH",
  message: "Lot LOT_1 on ETCH_007 is at 62% predicted low-yield risk.",
  created_at: "2026-09-14T14:39:16",
  status: "open",
  acknowledged_by: null,
  resolved_by: null,
};
const ACKED = {
  alert_id: "a2",
  lot_id: "LOT_2",
  severity: "MEDIUM",
  message: "Lot LOT_2 risk moved to MEDIUM.",
  created_at: "2026-09-13T10:00:00",
  status: "acknowledged",
  acknowledged_by: "engineer",
  resolved_by: null,
};
const RESOLVED = {
  alert_id: "a3",
  lot_id: "LOT_3",
  severity: "LOW",
  message: "Lot LOT_3 reviewed.",
  created_at: "2026-09-12T09:00:00",
  status: "resolved",
  acknowledged_by: "admin",
  resolved_by: "admin",
};

const TIMELINE = {
  alert_id: "a1",
  status: "open",
  events: [
    {
      alert_id: "a1",
      from_status: "open",
      to_status: "acknowledged",
      actor: { username: "engineer", role: "process_engineer" },
      at: "2026-09-14T15:00:00",
      note: "checking the tool",
    },
  ],
};

function mockApi() {
  get.mockImplementation((url, params = {}) => {
    if (url === "/alerts") {
      const alerts = [OPEN, ACKED, RESOLVED].filter((a) => (params.status ? a.status === params.status : true));
      return Promise.resolve({ alerts });
    }
    if (url === "/alerts/a1/timeline") return Promise.resolve(TIMELINE);
    if (url.endsWith("/timeline")) return Promise.resolve({ events: [] });
    if (url === "/alerts/deliveries") return Promise.resolve({ deliveries: [{ channel: "log", status: "logged", created_at: "2026-09-14T14:39:17" }] });
    return Promise.reject(new Error(`unexpected ${url}`));
  });
}

function setRole(role) {
  localStorage.setItem("wafguard_username", "engineer");
  localStorage.setItem("wafguard_role", role);
}

function renderAlerts() {
  return render(
    <MemoryRouter>
      <Alerts />
    </MemoryRouter>
  );
}

describe("Alerts triage page (Phase 8)", () => {
  beforeEach(() => {
    mockApi();
    setRole("process_engineer");
  });
  afterEach(() => {
    vi.resetAllMocks();
    localStorage.clear();
  });

  it("lists alerts with status badges and lot links", async () => {
    renderAlerts();
    await waitFor(() => expect(screen.getByText(/62% predicted low-yield risk/)).toBeInTheDocument());
    expect(screen.getByText("OPEN")).toBeInTheDocument();
    expect(screen.getByText("ACKNOWLEDGED")).toBeInTheDocument();
    expect(screen.getByText("RESOLVED")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "LOT_1" })).toHaveAttribute("href", "/lots/LOT_1");
    expect(get).toHaveBeenCalledWith("/alerts", { limit: 100 });
  });

  it("filters by status through the API params", async () => {
    renderAlerts();
    await waitFor(() => expect(screen.getByText(/62% predicted low-yield risk/)).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Filter by status"), { target: { value: "open" } });
    await waitFor(() => expect(get).toHaveBeenCalledWith("/alerts", { limit: 100, status: "open" }));
    // the mocked API applies the filter, so acknowledged/resolved rows disappear
    expect(screen.queryByText("RESOLVED")).not.toBeInTheDocument();
    expect(screen.queryByText("ACKNOWLEDGED")).not.toBeInTheDocument();
  });

  it("shows the audit timeline and deliveries when expanded", async () => {
    renderAlerts();
    await waitFor(() => expect(screen.getByText(/62% predicted low-yield risk/)).toBeInTheDocument());
    fireEvent.click(screen.getAllByText("Details")[0]);
    await waitFor(() => expect(screen.getByText(/checking the tool/)).toBeInTheDocument());
    expect(screen.getByText("open → acknowledged")).toBeInTheDocument();
    expect(screen.getByText(/logged/)).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith("/alerts/a1/timeline");
    expect(get).toHaveBeenCalledWith("/alerts/deliveries", { alert_id: "a1", limit: 20 });
  });

  it("acknowledges an open alert as an engineer", async () => {
    post.mockResolvedValue({ alert: { ...OPEN, status: "acknowledged" }, event: {} });
    renderAlerts();
    await waitFor(() => expect(screen.getByText(/62% predicted low-yield risk/)).toBeInTheDocument());
    fireEvent.click(screen.getAllByText("Details")[0]);
    fireEvent.click(await screen.findByText("Acknowledge"));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/alerts/a1/acknowledge", { note: null }));
    // list reloads after the mutation
    await waitFor(() => expect(get.mock.calls.filter((c) => c[0] === "/alerts").length).toBeGreaterThan(1));
  });

  it("offers Resolve for an acknowledged alert and a terminal notice for a resolved one", async () => {
    renderAlerts();
    await waitFor(() => expect(screen.getByText(/62% predicted low-yield risk/)).toBeInTheDocument());
    fireEvent.click(screen.getAllByText("Details")[1]); // the acknowledged alert
    expect(await screen.findByText("Resolve")).toBeInTheDocument();
    // expanding one row hides its "Details" button, so the remaining ones shift
    fireEvent.click(screen.getAllByText("Details")[1]); // now the resolved alert
    expect(await screen.findByText(/This alert is closed/)).toBeInTheDocument();
  });

  it("hides triage actions from viewers (read-only role)", async () => {
    setRole("viewer");
    renderAlerts();
    await waitFor(() => expect(screen.getByText(/62% predicted low-yield risk/)).toBeInTheDocument());
    fireEvent.click(screen.getAllByText("Details")[0]);
    await waitFor(() => expect(screen.getByText(/read-only/)).toBeInTheDocument());
    expect(screen.queryByText("Acknowledge")).not.toBeInTheDocument();
    expect(screen.queryByText("Resolve")).not.toBeInTheDocument();
    // reads still work for viewers
    expect(screen.getByText("OPEN")).toBeInTheDocument();
  });
});
