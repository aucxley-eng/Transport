import { useEffect, useMemo, useState } from "react";
import { Calendar, Clock, MapPin, RefreshCw, UserCheck, Car, AlertCircle } from "lucide-react";

import { useAuth } from "@/context/AuthContext";
import { listDepartures, type Departure } from "@/api/booking";
import { listVehicles, listDrivers, type Vehicle, type Driver } from "@/api/fleet";
import { listBranches, listRoutes, type Branch, type Route } from "@/api/setup";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Select } from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/toast";

const STATUS_COLORS: Record<string, "default" | "info" | "success" | "warning" | "danger"> = {
  scheduled: "info",
  boarding: "warning",
  departed: "success",
  completed: "default",
  cancelled: "danger",
};

export function DispatchBoardPage() {
  const { user } = useAuth();
  const { toast } = useToast();
  const [departures, setDepartures] = useState<Departure[]>([]);
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [drivers, setDrivers] = useState<Driver[]>([]);
  const [routes, setRoutes] = useState<Route[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [loading, setLoading] = useState(false);
  const [filterDate, setFilterDate] = useState(() => new Date().toISOString().split("T")[0]);
  const [filterRoute, setFilterRoute] = useState("all");
  const [filterBranch, setFilterBranch] = useState("all");

  const isOrgAdmin = useMemo(
    () => (user?.memberships ?? []).some((m) => m.is_active && ["operator_admin", "platform_admin"].includes(m.role)),
    [user]
  );

  const load = async () => {
    setLoading(true);
    try {
      const [deps, veh, drv, rts, brs] = await Promise.all([
        listDepartures({ departure_date: filterDate } as any),
        listVehicles(),
        listDrivers(),
        listRoutes(),
        listBranches().catch(() => []),
      ]);
      setDepartures(Array.isArray(deps) ? deps : (deps as any)?.results ?? []);
      setVehicles((Array.isArray(veh) ? veh : (veh as any)?.results ?? []).filter((v: Vehicle) => v.status === "available" || v.status === "active"));
      setDrivers((Array.isArray(drv) ? drv : (drv as any)?.results ?? []).filter((d: Driver) => d.status === "approved"));
      setRoutes(Array.isArray(rts) ? rts : (rts as any)?.results ?? []);
      setBranches(brs);
    } catch (e: any) {
      toast({ title: "Could not load dispatch board", description: e?.message, type: "error" });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [filterDate]);

  const routeMap = useMemo(() => {
    const m = new Map<string, Route>();
    for (const r of routes) {
      m.set(r.id, r);
    }
    return m;
  }, [routes]);

  const filtered = useMemo(() => {
    let list = departures;
    if (filterRoute !== "all") {
      list = list.filter((d) => d.route === filterRoute);
    }
    if (filterBranch !== "all") {
      list = list.filter((d) => d.branch === filterBranch);
    }
    return list.sort((a, b) => (a.departure_time || "").localeCompare(b.departure_time || ""));
  }, [departures, filterBranch, filterRoute]);

  const unassigned = filtered.filter((d) => !d.vehicle);
  const assigned = filtered.filter((d) => d.vehicle && d.status !== "departed" && d.status !== "completed");
  const inTransit = filtered.filter((d) => d.status === "departed");

  const getVehicleName = (id?: string | null) => {
    if (!id) return null;
    const v = vehicles.find((x) => x.id === id);
    return v ? `${v.registration_number} (${v.vehicle_class})` : id;
  };

  return (
    <>
      <header className="topbar">
        <div>
          <h1>Dispatch board</h1>
          <p className="muted mt-1">Assign vehicles and drivers, track departures in real time</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" onClick={() => void load()} disabled={loading}>
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </header>

      <div className="content stack">
        <Card padding="md">
          <div className="flex flex-wrap items-end gap-4">
            <div className="flex flex-col gap-1.5 min-w-[200px]">
              <Label>Date</Label>
              <input
                type="date"
                value={filterDate}
                onChange={(e) => setFilterDate(e.target.value)}
                className="h-9 rounded-md border border-[var(--border)] bg-transparent px-3 text-sm"
              />
            </div>
            <div className="flex flex-col gap-1.5 min-w-[200px]">
              <Label>Branch</Label>
              <Select value={filterBranch} onChange={(e) => setFilterBranch(e.target.value)}>
                <option value="all">All branches</option>
                {branches.map((branch) => (
                  <option key={branch.id} value={branch.id}>
                    {branch.name}
                  </option>
                ))}
              </Select>
            </div>
            <div className="flex flex-col gap-1.5 min-w-[200px]">
              <Label>Route</Label>
              <Select value={filterRoute} onChange={(e) => setFilterRoute(e.target.value)}>
                <option value="all">All routes</option>
                {routes.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
              </Select>
            </div>
            <div className="ml-auto flex items-center gap-4 text-sm">
              <div className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-amber-500" />
                <span className="text-[var(--text-secondary)]">Unassigned: {unassigned.length}</span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-blue-500" />
                <span className="text-[var(--text-secondary)]">Assigned: {assigned.length}</span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-emerald-500" />
                <span className="text-[var(--text-secondary)]">In transit: {inTransit.length}</span>
              </div>
            </div>
          </div>
        </Card>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* Unassigned */}
          <Card padding="lg">
            <CardHeader className="p-0 mb-4">
              <CardTitle className="flex items-center gap-2">
                <AlertCircle className="h-5 w-5 text-amber-500" />
                Unassigned
                <Badge variant="warning">{unassigned.length}</Badge>
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0 space-y-3">
              {unassigned.length === 0 && <p className="text-sm text-[var(--text-tertiary)]">No unassigned departures</p>}
              {unassigned.map((dep) => {
                const route = routeMap.get(dep.route);
                return (
                  <div key={dep.id} className="rounded-lg border border-[var(--border)] p-3 space-y-2">
                    <div className="flex items-start justify-between">
                      <div>
                        <p className="font-medium">{route?.name || dep.route_name || "Route"}</p>
                        <div className="flex items-center gap-2 text-xs text-[var(--text-tertiary)] mt-1">
                          <Clock className="h-3 w-3" />
                          <span>
                            {dep.departure_time} • {dep.capacity} seats{dep.branch_name ? ` · ${dep.branch_name}` : ""} • {dep.currency} {(dep.fare_minor / 100).toFixed(0)}
                          </span>
                        </div>
                      </div>
                      <Badge variant={STATUS_COLORS[dep.status] || "default"}>{dep.status}</Badge>
                    </div>
                    <div className="flex items-center gap-2 text-xs text-[var(--text-tertiary)]">
                      <Calendar className="h-3 w-3" />
                      <span>{dep.departure_date}</span>
                    </div>
                    {isOrgAdmin && (
                      <div className="pt-2 border-t border-[var(--border)]">
                        <p className="text-xs text-[var(--text-tertiary)] mb-2">Available vehicles/drivers</p>
                        <div className="flex flex-wrap gap-1">
                          {vehicles.slice(0, 3).map((v) => (
                            <Badge key={v.id} variant="info">
                              {v.registration_number}
                            </Badge>
                          ))}
                          {drivers.slice(0, 3).map((d) => (
                            <Badge key={d.id} variant="success">
                              {d.full_name || d.phone || "Driver"}
                            </Badge>
                          ))}
                        </div>
                        <p className="text-xs text-[var(--text-tertiary)] mt-2">Use routes/fleet setup to assign via backend or dispatch API</p>
                      </div>
                    )}
                  </div>
                );
              })}
            </CardContent>
          </Card>

          {/* Assigned */}
          <Card padding="lg">
            <CardHeader className="p-0 mb-4">
              <CardTitle className="flex items-center gap-2">
                <UserCheck className="h-5 w-5 text-blue-500" />
                Assigned
                <Badge variant="info">{assigned.length}</Badge>
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0 space-y-3">
              {assigned.length === 0 && <p className="text-sm text-[var(--text-tertiary)]">No assigned departures</p>}
              {assigned.map((dep) => {
                const route = routeMap.get(dep.route);
                return (
                  <div key={dep.id} className="rounded-lg border border-[var(--border)] p-3 space-y-2">
                    <div className="flex items-start justify-between">
                      <div>
                        <p className="font-medium">{route?.name || dep.route_name || "Route"}</p>
                        <div className="flex items-center gap-2 text-xs text-[var(--text-tertiary)] mt-1">
                          <Clock className="h-3 w-3" />
                          <span>
                            {dep.departure_time} • {dep.capacity} seats{dep.branch_name ? ` · ${dep.branch_name}` : ""}
                          </span>
                        </div>
                      </div>
                      <Badge variant={STATUS_COLORS[dep.status] || "default"}>{dep.status}</Badge>
                    </div>
                    {dep.vehicle && (
                      <div className="flex items-center gap-2 text-sm">
                        <Car className="h-4 w-4 text-[var(--text-tertiary)]" />
                        <span>{getVehicleName(dep.vehicle)}</span>
                      </div>
                    )}
                    <div className="flex items-center gap-2 text-xs text-[var(--text-tertiary)]">
                      <Calendar className="h-3 w-3" />
                      <span>{dep.departure_date}</span>
                    </div>
                  </div>
                );
              })}
            </CardContent>
          </Card>

          {/* In Transit */}
          <Card padding="lg">
            <CardHeader className="p-0 mb-4">
              <CardTitle className="flex items-center gap-2">
                <MapPin className="h-5 w-5 text-emerald-500" />
                In transit
                <Badge variant="success">{inTransit.length}</Badge>
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0 space-y-3">
              {inTransit.length === 0 && <p className="text-sm text-[var(--text-tertiary)]">No departures in transit</p>}
              {inTransit.map((dep) => {
                const route = routeMap.get(dep.route);
                return (
                  <div key={dep.id} className="rounded-lg border border-emerald-200 dark:border-emerald-800 bg-emerald-50/30 dark:bg-emerald-950/20 p-3 space-y-2">
                    <div className="flex items-start justify-between">
                      <div>
                        <p className="font-medium">{route?.name || dep.route_name || "Route"}</p>
                        <div className="flex items-center gap-2 text-xs text-[var(--text-tertiary)] mt-1">
                          <Clock className="h-3 w-3" />
                          <span>
                            {dep.departure_time} • {dep.capacity} seats{dep.branch_name ? ` · ${dep.branch_name}` : ""}
                          </span>
                        </div>
                      </div>
                      <Badge variant="success">{dep.status}</Badge>
                    </div>
                    {dep.vehicle && (
                      <div className="flex items-center gap-2 text-sm">
                        <Car className="h-4 w-4 text-[var(--text-tertiary)]" />
                        <span>{getVehicleName(dep.vehicle)}</span>
                      </div>
                    )}
                  </div>
                );
              })}
            </CardContent>
          </Card>
        </div>
      </div>
    </>
  );
}
