import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { post } from "../services/api";

export default function Login() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  async function handleSubmit(e) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const data = await post("/auth/login", { username, password });
      localStorage.setItem("wafguard_token", data.access_token);
      localStorage.setItem("wafguard_username", data.username);
      localStorage.setItem("wafguard_role", data.role);
      navigate("/dashboard");
    } catch (err) {
      setError(err.message || "Login failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-surface px-4">
      <div className="w-full max-w-sm panel p-6">
        <div className="flex items-center gap-2 mb-6">
          <div className="w-9 h-9 rounded-md bg-brand/15 border border-brand/30 flex items-center justify-center text-brand font-mono font-bold">W</div>
          <div>
            <div className="font-semibold text-ink-primary">WafGuard AI</div>
            <div className="text-[11px] text-ink-muted">Predict. Diagnose. Prevent. Improve Yield.</div>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="label-caps block mb-1.5">Username</label>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              className="w-full bg-surface-raised border border-surface-border rounded-md px-3 py-2 text-sm text-ink-primary focus:outline-none focus:border-brand/50"
            />
          </div>
          <div>
            <label className="label-caps block mb-1.5">Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              className="w-full bg-surface-raised border border-surface-border rounded-md px-3 py-2 text-sm text-ink-primary focus:outline-none focus:border-brand/50"
            />
          </div>
          {error && <p className="text-xs text-status-critical">{error}</p>}
          <button
            type="submit"
            disabled={loading}
            className="w-full py-2 rounded-md bg-brand text-surface font-medium text-sm hover:bg-brand/90 disabled:opacity-50"
          >
            {loading ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <div className="mt-5 pt-4 border-t border-surface-border text-[11px] text-ink-muted">
          Demo accounts: admin / wafguard-admin · engineer / wafguard-engineer · viewer / wafguard-viewer
        </div>
      </div>
    </div>
  );
}
