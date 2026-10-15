import axios from "axios";

// Same-origin `/api` is the default so Vite (dev) and nginx (Docker) can
// proxy to FastAPI. Override with VITE_API_BASE_URL to hit the API directly.
const baseURL = import.meta.env.VITE_API_BASE_URL || "/api";

export const api = axios.create({
  baseURL,
  timeout: 20000,
});

function isLoginRequest(config) {
  const url = `${config?.baseURL || ""}${config?.url || ""}`;
  return url.includes("/auth/login");
}

function clearSession() {
  localStorage.removeItem("wafguard_token");
  localStorage.removeItem("wafguard_username");
  localStorage.removeItem("wafguard_role");
}

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("wafguard_token");
  if (token) {
    config.headers = config.headers || {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error.response?.status;
    if (status === 401 && !isLoginRequest(error.config) && typeof window !== "undefined") {
      clearSession();
      if (window.location.pathname !== "/login") {
        window.location.assign("/login");
      }
    }
    return Promise.reject(error);
  }
);

/**
 * Unwraps the {success, data, error, timestamp} envelope every WafGuard
 * endpoint returns. Throws a normalized Error with `.code` and `.status`
 * so components can branch on error type instead of parsing Axios errors.
 */
export async function apiRequest(config) {
  try {
    const response = await api.request(config);
    const envelope = response.data;
    if (!envelope.success) {
      const err = new Error(envelope.error?.message || "Request failed");
      err.code = envelope.error?.code || "UNKNOWN_ERROR";
      err.status = response.status;
      throw err;
    }
    return envelope.data;
  } catch (caught) {
    if (caught.code) throw caught; // already normalized above
    const envelope = caught.response?.data;
    const err = new Error(envelope?.error?.message || caught.message || "Network error");
    err.code = envelope?.error?.code || "NETWORK_ERROR";
    err.status = caught.response?.status;
    throw err;
  }
}

export const get = (url, params) => apiRequest({ method: "GET", url, params });
export const post = (url, data, config = {}) => apiRequest({ method: "POST", url, data, ...config });
