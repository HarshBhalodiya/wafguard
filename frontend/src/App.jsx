import { Navigate, Route, Routes } from "react-router-dom";
import AppLayout from "./layouts/AppLayout.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Lots from "./pages/Lots.jsx";
import LotDetails from "./pages/LotDetails.jsx";
import YieldAnalytics from "./pages/YieldAnalytics.jsx";
import RootCause from "./pages/RootCause.jsx";
import DefectAnalyzer from "./pages/DefectAnalyzer.jsx";
import WaferMapPage from "./pages/WaferMapPage.jsx";
import RiskMonitor from "./pages/RiskMonitor.jsx";
import Alerts from "./pages/Alerts.jsx";
import Equipment from "./pages/Equipment.jsx";
import ControlCharts from "./pages/ControlCharts.jsx";
import Recommendations from "./pages/Recommendations.jsx";
import SimilarLots from "./pages/SimilarLots.jsx";
import WhatIf from "./pages/WhatIf.jsx";
import ModelPerformance from "./pages/ModelPerformance.jsx";
import Settings from "./pages/Settings.jsx";
import Login from "./pages/Login.jsx";
import LotInvestigation from "./pages/LotInvestigation.jsx";
import UpcomingLots from "./pages/UpcomingLots.jsx";
import RootCauseExplorer from "./pages/RootCauseExplorer.jsx";
import ModelDataHealth from "./pages/ModelDataHealth.jsx";
import RequireAuth from "./components/RequireAuth.jsx";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<RequireAuth />}>
        <Route element={<AppLayout />}>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />
          {/* Phase 10: decision-centred screens. The older routes below stay reachable. */}
          <Route path="/investigate" element={<LotInvestigation />} />
          <Route path="/upcoming" element={<UpcomingLots />} />
          <Route path="/root-cause-explorer" element={<RootCauseExplorer />} />
          <Route path="/health" element={<ModelDataHealth />} />
          <Route path="/lots" element={<Lots />} />
          <Route path="/lots/:lotId" element={<LotDetails />} />
          <Route path="/yield-analytics" element={<YieldAnalytics />} />
          <Route path="/root-cause" element={<RootCause />} />
          <Route path="/defect-analyzer" element={<DefectAnalyzer />} />
          <Route path="/wafer-map" element={<WaferMapPage />} />
          <Route path="/risk-monitor" element={<RiskMonitor />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/equipment" element={<Equipment />} />
          <Route path="/control-charts" element={<ControlCharts />} />
          <Route path="/recommendations" element={<Recommendations />} />
          <Route path="/similar-lots" element={<SimilarLots />} />
          <Route path="/what-if" element={<WhatIf />} />
          <Route path="/model-performance" element={<ModelPerformance />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Route>
      </Route>
    </Routes>
  );
}
