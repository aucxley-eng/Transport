import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { ErrorBoundary } from "@/components/ErrorBoundary";
import { AppLayout } from "@/components/layout/AppLayout";
import { RequireAuth } from "@/components/RequireAuth";
import { AuthProvider } from "@/context/AuthContext";
import { ToastProvider } from "@/components/ui/toast";
import { LoginPage } from "@/pages/auth/LoginPage";
import { DispatchPage } from "@/pages/dispatch/DispatchPage";
import { DispatchBoardPage } from "@/pages/dispatch/DispatchBoardPage";
import { StaffPage } from "@/pages/people/StaffPage";
import { CounterPage } from "@/pages/counter/CounterPage";
import { CompanySetupPage } from "@/pages/setup/CompanySetupPage";
import { FleetSetupPage } from "@/pages/fleet/FleetSetupPage";
import { RoutesSetupPage } from "@/pages/routes/RoutesSetupPage";
import { OrganizationDetailPage } from "@/pages/organisations/OrganizationDetailPage";
import { BranchesPage } from "@/pages/branches/BranchesPage";
import { BranchDetailPage } from "@/pages/branches/BranchDetailPage";
import { PlaceholderPage } from "@/pages/placeholder/PlaceholderPage";

export function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <AuthProvider>
          <ToastProvider>
            <Routes>
              <Route path="/login" element={<LoginPage />} />

              <Route
                element={
                  <RequireAuth>
                    <AppLayout />
                  </RequireAuth>
                }
              >
                <Route path="/dispatch" element={<DispatchPage />} />
                <Route path="/people" element={<StaffPage />} />
                <Route path="/counter" element={<CounterPage />} />
                <Route path="/setup/company" element={<CompanySetupPage />} />
                <Route path="/organisations/:id" element={<OrganizationDetailPage />} />
                <Route path="/setup/fleet" element={<FleetSetupPage />} />
                <Route path="/setup/routes" element={<RoutesSetupPage />} />
                <Route path="/dispatch/board" element={<DispatchBoardPage />} />
                <Route path="/passenger" element={<PlaceholderPage title="Passenger" description="Manage scheduled passenger services." />} />
                <Route path="/goods" element={<PlaceholderPage title="Goods and hubs" description="Shipments, hubs and line-haul." />} />
                <Route path="/branches" element={<BranchesPage />} />
                <Route path="/branches/:id" element={<BranchDetailPage />} />
                <Route path="/finance/statements" element={<PlaceholderPage title="Statements" description="Revenue and reconciliation." />} />
                <Route path="/finance/collections" element={<PlaceholderPage title="Collections" description="Collections and cash-up." />} />
                <Route path="/finance/payouts" element={<PlaceholderPage title="Payouts" description="Driver and partner payouts." />} />
                <Route path="/support/cases" element={<PlaceholderPage title="Cases" description="Customer support cases." />} />
                <Route path="/support/safety" element={<PlaceholderPage title="Safety and appeals" description="Incidents, safety and appeals." />} />
                <Route path="/admin/audit" element={<PlaceholderPage title="Audit log" description="System activity and changes." />} />
              </Route>

              <Route path="*" element={<Navigate to="/dispatch" replace />} />
            </Routes>
          </ToastProvider>
        </AuthProvider>
      </BrowserRouter>
    </ErrorBoundary>
  );
}
