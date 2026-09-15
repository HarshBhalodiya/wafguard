import axios from "axios";

const baseURL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000/api";

export const api = axios.create({
  baseURL,
  timeout: 20000,
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("wafguard_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

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
