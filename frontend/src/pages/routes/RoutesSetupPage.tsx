import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Map, Plus, Route as RouteIcon } from "lucide-react";
import { useSearchParams } from "react-router-dom";

import { useAuth } from "@/context/AuthContext";
import {
  createDeparture,
  createRoute,
  createRouteStop,
  listBranches,
  listOrganizations,
  listPlaces,
  listRoutes,
  listServices,
  listVehicles,
  type Branch,
  type Organization,
  type Place,
  type Route,
  type Service,
  type Vehicle,
} from "@/api/setup";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/toast";

export function RoutesSetupPage() {
  const { user } = useAuth();
  const { toast } = useToast();
  const [searchParams] = useSearchParams();
  const requestedOrg = searchParams.get("org") ?? "";
  const requestedBranch = searchParams.get("branch") ?? "";
  const [orgs, setOrgs] = useState<Organization[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [places, setPlaces] = useState<Place[]>([]);
  const [routes, setRoutes] = useState<Route[]>([]);
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [services, setServices] = useState<Service[]>([]);
  const [, setLoading] = useState(false);

  const [org, setOrg] = useState(requestedOrg);
  const [routeBranch, setRouteBranch] = useState(requestedBranch);
  const [routeName, setRouteName] = useState("");
  const [creatingRoute, setCreatingRoute] = useState(false);
  const [selectedRoute, setSelectedRoute] = useState("");
  const [stopPlace, setStopPlace] = useState("");
  const [stopPos, setStopPos] = useState(1);
  const [addingStop, setAddingStop] = useState(false);

  const [depRoute, setDepRoute] = useState("");
  const [depBranch, setDepBranch] = useState(requestedBranch);
  const [depVehicle, setDepVehicle] = useState("");
  const [depDate, setDepDate] = useState("");
  const [depTime, setDepTime] = useState("");
  const [depCap, setDepCap] = useState(33);
  const [depFare, setDepFare] = useState(1000);
  const [depCurrency, setDepCurrency] = useState("KES");
  const [depService, setDepService] = useState("");
  const [creatingDep, setCreatingDep] = useState(false);

  const canSetup = useMemo(
    () => (user?.memberships ?? []).some((m) => m.is_active && ["operator_admin", "platform_admin"].includes(m.role)),
    [user]
  );

  const scheduledService = useMemo(
    () => services.find((s) => s.service_type === "scheduled") ?? services[0] ?? null,
    [services]
  );

  const load = async () => {
    setLoading(true);
    try {
      const [o, p, r, v, s, b] = await Promise.all([
        listOrganizations(),
        listPlaces(),
        listRoutes(),
        listVehicles(),
        listServices(),
        listBranches().catch(() => []),
      ]);
      setOrgs(o);
      setBranches(b);
      setPlaces(p);
      setRoutes(r);
      setVehicles(v);
      setServices(s);
      const m = user?.memberships.find((x) => x.is_active);
      const orgId = requestedOrg || m?.organization || o[0]?.id || "";
      if (requestedOrg) setOrg(requestedOrg);
      else if (!org) setOrg(orgId);

      if (scheduledService) setDepService(scheduledService.id);
    } catch (e: any) {
      toast({ title: "Could not load routes setup", description: e?.message, type: "error" });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const branchOptions = useMemo(
    () => branches.filter((row) => row.organization === org && row.is_active),
    [branches, org]
  );

  useEffect(() => {
    if (routeBranch && !branchOptions.some((row) => row.id === routeBranch)) {
      setRouteBranch("");
    }
    if (depBranch && !branchOptions.some((row) => row.id === depBranch)) {
      setDepBranch("");
    }
  }, [branchOptions, depBranch, routeBranch]);

  async function onCreateRoute(e: FormEvent) {
    e.preventDefault();
    setCreatingRoute(true);
    try {
      const created = await createRoute({
        organization: org,
        branch: routeBranch || null,
        name: routeName,
        is_active: true,
      });
      toast({ title: "Route created", description: created.name, type: "success" });
      setRouteName("");
      setSelectedRoute(created.id);
      setDepRoute(created.id);
      await load();
    } catch (e: any) {
      toast({ title: "Failed to create route", description: e?.response?.data?.detail || e?.message, type: "error" });
    } finally {
      setCreatingRoute(false);
    }
  }

  async function onAddStop(e: FormEvent) {
    e.preventDefault();
    setAddingStop(true);
    try {
      await createRouteStop({ route: selectedRoute, place: stopPlace, position: stopPos });
      toast({ title: "Stop added", description: "Route stop saved", type: "success" });
      setStopPos((p) => p + 1);
    } catch (e: any) {
      toast({ title: "Failed to add stop", description: e?.response?.data?.detail || e?.message, type: "error" });
    } finally {
      setAddingStop(false);
    }
  }

  async function onCreateDep(e: FormEvent) {
    e.preventDefault();
    setCreatingDep(true);
    try {
      await createDeparture({
        organization: org,
        branch: depBranch || undefined,
        route: depRoute,
        vehicle: depVehicle || undefined,
        departure_date: depDate,
        departure_time: depTime,
        capacity: depCap,
        fare_minor: Math.round(depFare * 100),
        currency: depCurrency,
        status: "scheduled",
        vehicle_class: vehicles.find((v) => v.id === depVehicle)?.vehicle_class,
      });
      toast({ title: "Departure scheduled", description: `${depDate} ${depTime}`, type: "success" });
      setDepDate("");
      setDepTime("");
    } catch (e: any) {
      toast({ title: "Failed to schedule departure", description: e?.response?.data?.detail || e?.message, type: "error" });
    } finally {
      setCreatingDep(false);
    }
  }

  if (!canSetup) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-10 sm:px-6 lg:px-8">
        <Card>
          <CardHeader>
            <CardTitle>Routes access restricted</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-[color:var(--text-subtle)]">
            Only operator or platform administrators can create routes and departures.
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
      <header>
        <p className="text-xs uppercase tracking-[0.18em] text-[color:var(--text-subtle)]">Routes & departures</p>
        <h1 className="text-2xl font-semibold tracking-tight text-[color:var(--text)]">Routes setup</h1>
        <p className="mt-1 text-sm text-[color:var(--text-subtle)]">Define corridors, stops and schedule departures for ticket sales.</p>
      </header>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <RouteIcon className="h-4 w-4" />
              Create route
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <form onSubmit={onCreateRoute} className="flex flex-col gap-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label>Organization</Label>
                  <Select value={org} onChange={(e) => setOrg(e.target.value)}>
                    {orgs.map((o) => (
                      <option key={o.id} value={o.id}>{o.name}</option>
                    ))}
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Route name</Label>
                  <Input value={routeName} onChange={(e) => setRouteName(e.target.value)} placeholder="Nairobi - Mombasa" required />
                </div>
              </div>
              <div className="space-y-2">
                <Label>Route owner</Label>
                <Select value={routeBranch} onChange={(e) => setRouteBranch(e.target.value)}>
                  <option value="">Network-wide (shared by all branches)</option>
                  {branchOptions.map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.name}
                    </option>
                  ))}
                </Select>
              </div>
              <Button type="submit" disabled={creatingRoute || !org} className="gap-2">
                <Plus className="h-4 w-4" />
                {creatingRoute ? "Creating..." : "Create route"}
              </Button>
            </form>

            <div className="rounded-xl border border-[color:var(--surface-border)] p-3">
              <p className="mb-2 text-sm font-medium">Add stops (in order)</p>
              <form onSubmit={onAddStop} className="grid gap-3 sm:grid-cols-[1fr_120px_auto]">
                <div className="space-y-2">
                  <Label>Route</Label>
                  <Select value={selectedRoute} onChange={(e) => setSelectedRoute(e.target.value)}>
                    <option value="">Select route</option>
                    {routes.map((r) => (
                      <option key={r.id} value={r.id}>{r.name}</option>
                    ))}
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Position</Label>
                  <Input type="number" min={1} value={stopPos} onChange={(e) => setStopPos(Number(e.target.value))} />
                </div>
                <div className="flex items-end">
                  <Button type="submit" disabled={addingStop || !selectedRoute || !stopPlace}>Add stop</Button>
                </div>
                <div className="sm:col-span-3 space-y-2">
                  <Label>Place / stage</Label>
                  <Select value={stopPlace} onChange={(e) => setStopPlace(e.target.value)}>
                    <option value="">Select place</option>
                    {places.map((p) => (
                      <option key={p.id} value={p.id}>{p.name} ({p.place_type})</option>
                    ))}
                  </Select>
                </div>
              </form>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Map className="h-4 w-4" />
              Schedule departure
            </CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={onCreateDep} className="flex flex-col gap-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label>Organization</Label>
                  <Select value={org} onChange={(e) => setOrg(e.target.value)}>
                    {orgs.map((o) => (
                      <option key={o.id} value={o.id}>{o.name}</option>
                    ))}
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Route</Label>
                  <Select
                    value={depRoute}
                    onChange={(e) => {
                      setDepRoute(e.target.value);
                      const route = routes.find((row) => row.id === e.target.value);
                      if (route?.branch) setDepBranch(route.branch);
                    }}
                    required
                  >
                    <option value="">Select route</option>
                    {routes.map((r) => (
                      <option key={r.id} value={r.id}>{r.name}</option>
                    ))}
                  </Select>
                </div>
              </div>
              <div className="grid gap-3 sm:grid-cols-3">
                <div className="space-y-2">
                  <Label>Operating branch</Label>
                  <Select value={depBranch} onChange={(e) => setDepBranch(e.target.value)}>
                    <option value="">Use route branch or head office</option>
                    {branchOptions.map((row) => (
                      <option key={row.id} value={row.id}>
                        {row.name}
                      </option>
                    ))}
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Vehicle</Label>
                  <Select value={depVehicle} onChange={(e) => setDepVehicle(e.target.value)}>
                    <option value="">Assign later</option>
                    {vehicles.map((v) => (
                      <option key={v.id} value={v.id}>{v.registration_number} ({v.vehicle_class})</option>
                    ))}
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Service</Label>
                  <Select value={depService} onChange={(e) => setDepService(e.target.value)}>
                    {services.map((s) => (
                      <option key={s.id} value={s.id}>{s.name}</option>
                    ))}
                  </Select>
                </div>
              </div>
              <div className="grid gap-3 sm:grid-cols-3">
                <div className="space-y-2">
                  <Label>Date</Label>
                  <Input type="date" value={depDate} onChange={(e) => setDepDate(e.target.value)} required />
                </div>
                <div className="space-y-2">
                  <Label>Time</Label>
                  <Input type="time" value={depTime} onChange={(e) => setDepTime(e.target.value)} required />
                </div>
                <div className="space-y-2">
                  <Label>Capacity</Label>
                  <Input type="number" min={1} value={depCap} onChange={(e) => setDepCap(Number(e.target.value))} required />
                </div>
              </div>
              <div className="grid gap-3 sm:grid-cols-3">
                <div className="space-y-2">
                  <Label>Fare (KES)</Label>
                  <Input type="number" min={0} value={depFare} onChange={(e) => setDepFare(Number(e.target.value))} required />
                </div>
                <div className="space-y-2">
                  <Label>Currency</Label>
                  <Input value={depCurrency} onChange={(e) => setDepCurrency(e.target.value)} />
                </div>
              </div>
              <Button type="submit" disabled={creatingDep || !depRoute} className="gap-2">
                <Plus className="h-4 w-4" />
                {creatingDep ? "Scheduling..." : "Schedule departure"}
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
