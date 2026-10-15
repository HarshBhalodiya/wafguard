import { Navigate, Outlet, useLocation } from "react-router-dom";

export default function RequireAuth() {
  const token = localStorage.getItem("wafguard_token");
  const location = useLocation();
  if (!token) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }
  return <Outlet />;
}
