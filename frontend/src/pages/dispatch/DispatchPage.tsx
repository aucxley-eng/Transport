import { useAuth } from "@/context/AuthContext";
import { 
  Users, 
  Car, 
  Package, 
  Clock,
  TrendingUp,
  Activity,
  MapPin,
  Calendar,
  DollarSign,
  AlertCircle,
  CheckCircle,
  Truck
} from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export function DispatchPage() {
  const { user } = useAuth();
  const activeMemberships = user?.memberships.filter((m) => m.is_active) ?? [];
  const isSuperuser = user?.memberships?.some(m => m.role === 'platform_admin') || false;

  return (
    <>
      <header className="topbar">
        <div>
          <h1>Operations Overview</h1>
          <p className="muted mt-1">Platform-wide transport & logistics management</p>
        </div>
        <div className="flex items-center gap-3">
          <Button variant="secondary" size="sm">
            <Activity className="h-4 w-4" />
            Refresh Data
          </Button>
          <Button variant="primary" size="sm">
            <Calendar className="h-4 w-4" />
            Today's Report
          </Button>
        </div>
      </header>

      <div className="content stack">
        {/* Welcome Card */}
        <Card padding="lg" className="bg-gradient-to-r from-[var(--primary)]/5 to-transparent">
          <div className="flex items-start justify-between gap-4 flex-wrap">
            <div>
              <h2 className="text-xl font-semibold mb-2 flex items-center gap-2">
                Welcome back, {user?.full_name?.split(' ')[0] || user?.phone}
                {isSuperuser && <Badge variant="info">Platform Admin</Badge>}
              </h2>
              <p className="text-[var(--text-secondary)]">
                {isSuperuser 
                  ? "Full platform oversight and management" 
                  : `Managing operations for ${activeMemberships.length} organization(s)`}
              </p>
            </div>
            <Badge variant="success" className="flex items-center gap-1">
              <div className="w-1.5 h-1.5 rounded-full bg-current animate-pulse"></div>
              System Online
            </Badge>
          </div>
        </Card>

        {/* KPI Stats */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          <Card padding="md" className="hover:shadow-lg transition-all cursor-pointer group">
            <div className="flex items-center justify-between mb-3">
              <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-blue-500/10 to-blue-600/10 flex items-center justify-center group-hover:scale-110 transition-transform">
                <Car className="h-6 w-6 text-blue-600" />
              </div>
              <Badge variant="info">Live</Badge>
            </div>
            <div>
              <p className="text-sm text-[var(--text-tertiary)] mb-1">Active Rides</p>
              <p className="text-3xl font-bold text-[var(--text-primary)] mb-1">0</p>
              <p className="text-xs text-[var(--text-tertiary)] flex items-center gap-1">
                <Clock className="h-3 w-3" />
                No active trips
              </p>
            </div>
          </Card>

          <Card padding="md" className="hover:shadow-lg transition-all cursor-pointer group">
            <div className="flex items-center justify-between mb-3">
              <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-amber-500/10 to-orange-600/10 flex items-center justify-center group-hover:scale-110 transition-transform">
                <Package className="h-6 w-6 text-amber-600" />
              </div>
              <Badge variant="warning">Pending</Badge>
            </div>
            <div>
              <p className="text-sm text-[var(--text-tertiary)] mb-1">Shipments</p>
              <p className="text-3xl font-bold text-[var(--text-primary)] mb-1">0</p>
              <p className="text-xs text-[var(--text-tertiary)]">In transit / processing</p>
            </div>
          </Card>

          <Card padding="md" className="hover:shadow-lg transition-all cursor-pointer group">
            <div className="flex items-center justify-between mb-3">
              <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-emerald-500/10 to-green-600/10 flex items-center justify-center group-hover:scale-110 transition-transform">
                <Users className="h-6 w-6 text-emerald-600" />
              </div>
              <Badge variant="success">Online</Badge>
            </div>
            <div>
              <p className="text-sm text-[var(--text-tertiary)] mb-1">Drivers Available</p>
              <p className="text-3xl font-bold text-[var(--text-primary)] mb-1">0</p>
              <p className="text-xs text-[var(--text-tertiary)]">Ready for dispatch</p>
            </div>
          </Card>

          <Card padding="md" className="hover:shadow-lg transition-all cursor-pointer group">
            <div className="flex items-center justify-between mb-3">
              <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-purple-500/10 to-indigo-600/10 flex items-center justify-center group-hover:scale-110 transition-transform">
                <DollarSign className="h-6 w-6 text-purple-600" />
              </div>
              <Badge variant="default">Today</Badge>
            </div>
            <div>
              <p className="text-sm text-[var(--text-tertiary)] mb-1">Revenue</p>
              <p className="text-3xl font-bold text-[var(--text-primary)] mb-1">KES 0</p>
              <p className="text-xs text-[var(--text-tertiary)] flex items-center gap-1">
                <TrendingUp className="h-3 w-3" />
                vs yesterday
              </p>
            </div>
          </Card>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Quick Actions */}
          <Card padding="lg" className="lg:col-span-2">
            <div className="flex items-center justify-between mb-6">
              <div>
                <h2 className="text-lg font-semibold">Quick Actions</h2>
                <p className="text-sm text-[var(--text-tertiary)] mt-1">Common operations</p>
              </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <button className="flex items-center gap-3 p-4 rounded-xl border border-[var(--border)] hover:border-[var(--primary)] hover:bg-[var(--primary)]/5 transition-all text-left group">
                <div className="w-10 h-10 rounded-lg bg-blue-100 flex items-center justify-center group-hover:scale-110 transition-transform">
                  <Car className="h-5 w-5 text-blue-600" />
                </div>
                <div>
                  <p className="font-medium text-[var(--text-primary)]">Dispatch Rides</p>
                  <p className="text-xs text-[var(--text-tertiary)]">Manage ride requests & trips</p>
                </div>
              </button>
              <button className="flex items-center gap-3 p-4 rounded-xl border border-[var(--border)] hover:border-[var(--primary)] hover:bg-[var(--primary)]/5 transition-all text-left group">
                <div className="w-10 h-10 rounded-lg bg-amber-100 flex items-center justify-center group-hover:scale-110 transition-transform">
                  <Package className="h-5 w-5 text-amber-600" />
                </div>
                <div>
                  <p className="font-medium text-[var(--text-primary)]">Shipments</p>
                  <p className="text-xs text-[var(--text-tertiary)]">Track parcels & freight</p>
                </div>
              </button>
              <button className="flex items-center gap-3 p-4 rounded-xl border border-[var(--border)] hover:border-[var(--primary)] hover:bg-[var(--primary)]/5 transition-all text-left group">
                <div className="w-10 h-10 rounded-lg bg-emerald-100 flex items-center justify-center group-hover:scale-110 transition-transform">
                  <Truck className="h-5 w-5 text-emerald-600" />
                </div>
                <div>
                  <p className="font-medium text-[var(--text-primary)]">Fleet Management</p>
                  <p className="text-xs text-[var(--text-tertiary)]">Vehicles & drivers</p>
                </div>
              </button>
              <button className="flex items-center gap-3 p-4 rounded-xl border border-[var(--border)] hover:border-[var(--primary)] hover:bg-[var(--primary)]/5 transition-all text-left group">
                <div className="w-10 h-10 rounded-lg bg-purple-100 flex items-center justify-center group-hover:scale-110 transition-transform">
                  <DollarSign className="h-5 w-5 text-purple-600" />
                </div>
                <div>
                  <p className="font-medium text-[var(--text-primary)]">Payments & Finance</p>
                  <p className="text-xs text-[var(--text-tertiary)]">Revenue & settlements</p>
                </div>
              </button>
            </div>
          </Card>

          {/* System Status */}
          <Card padding="lg">
            <div className="mb-6">
              <h2 className="text-lg font-semibold">System Status</h2>
              <p className="text-sm text-[var(--text-tertiary)] mt-1">Platform health</p>
            </div>
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <CheckCircle className="h-4 w-4 text-emerald-500" />
                  <span className="text-sm text-[var(--text-secondary)]">API Services</span>
                </div>
                <Badge variant="success">Operational</Badge>
              </div>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <CheckCircle className="h-4 w-4 text-emerald-500" />
                  <span className="text-sm text-[var(--text-secondary)]">Database</span>
                </div>
                <Badge variant="success">Connected</Badge>
              </div>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <AlertCircle className="h-4 w-4 text-amber-500" />
                  <span className="text-sm text-[var(--text-secondary)]">Redis/Cache</span>
                </div>
                <Badge variant="warning">Not Configured</Badge>
              </div>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <CheckCircle className="h-4 w-4 text-emerald-500" />
                  <span className="text-sm text-[var(--text-secondary)]">Payments</span>
                </div>
                <Badge variant="success">Ready</Badge>
              </div>
            </div>
          </Card>
        </div>

        {/* Organizations & Access */}
        <Card padding="lg">
          <div className="flex items-center justify-between mb-6">
            <div>
              <h2 className="text-lg font-semibold">Your Organizations & Access</h2>
              <p className="text-sm text-[var(--text-tertiary)] mt-1">Manage permissions and scope</p>
            </div>
            <Badge variant="default">{activeMemberships.length} Active</Badge>
          </div>
          {activeMemberships.length > 0 ? (
            <div className="overflow-x-auto">
              <table>
                <thead>
                  <tr>
                    <th>Organization</th>
                    <th>Role</th>
                    <th>Type</th>
                    <th>Status</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {user?.memberships.map((membership: any) => (
                    <tr key={membership.id}>
                      <td>
                        <div className="flex items-center gap-3">
                          <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-[var(--primary)]/10 to-[var(--primary)]/5 flex items-center justify-center">
                            <MapPin className="h-5 w-5 text-[var(--primary)]" />
                          </div>
                          <div>
                            <p className="font-medium">{membership.organization_name}</p>
                            <p className="text-xs text-[var(--text-tertiary)]">ID: {membership.organization?.slice(0, 8)}...</p>
                          </div>
                        </div>
                      </td>
                      <td>
                        <Badge variant="info" className="capitalize">
                          {membership.role.replace('_', ' ')}
                        </Badge>
                      </td>
                      <td className="text-[var(--text-secondary)]">Operator</td>
                      <td>
                        <Badge variant={membership.is_active ? "success" : "default"}>
                          {membership.is_active ? "Active" : "Inactive"}
                        </Badge>
                      </td>
                      <td>
                        <Button variant="ghost" size="sm">Manage</Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="text-center py-12">
              <Users className="h-12 w-12 mx-auto text-[var(--text-tertiary)] mb-3" />
              <p className="text-[var(--text-secondary)] font-medium mb-1">No active memberships</p>
              <p className="text-sm text-[var(--text-tertiary)]">
                Contact your platform administrator for access
              </p>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
