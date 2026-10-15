import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import RequireAuth from "./RequireAuth.jsx";

function renderWithAuth(initialPath = "/dashboard") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/login" element={<div>Login page</div>} />
        <Route element={<RequireAuth />}>
          <Route path="/dashboard" element={<div>Protected dashboard</div>} />
        </Route>
      </Routes>
    </MemoryRouter>
  );
}

describe("RequireAuth", () => {
  afterEach(() => localStorage.clear());

  it("redirects to /login when no token is stored", () => {
    localStorage.clear();
    renderWithAuth();
    expect(screen.getByText("Login page")).toBeInTheDocument();
    expect(screen.queryByText("Protected dashboard")).not.toBeInTheDocument();
  });

  it("renders the protected route when a token is stored", () => {
    localStorage.setItem("wafguard_token", "demo-token");
    renderWithAuth();
    expect(screen.getByText("Protected dashboard")).toBeInTheDocument();
  });
});
