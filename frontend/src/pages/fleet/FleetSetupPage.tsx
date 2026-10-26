import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Car, Plus, Users } from "lucide-react";
import { useSearchParams } from "react-router-dom";

import { useAuth } from "@/context/AuthContext";
import { createVehicle, listBranches, listOrganizations, listVehicles, type Branch, type Organization, type Vehicle } from "@/api/setup";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/toast";

const CLASSES = [
  { value: "bus", label: "Bus" },
  { value: "shuttle", label: "Shuttle" },
  { value: "van", label: "Van" },
  { value: "sedan", label: "Sedan" },
  { value: "truck", label: "Truck" },
  { value: "trailer", label: "Trailer" },
];

export function FleetSetupPage() {
  const { user } = useAuth();
  const { toast } = useToast();
  const [searchParams] = useSearchParams();
  const requestedOrg = searchParams.get("org") ?? "";
  const requestedBranch = searchParams.get("branch") ?? "";
  const [orgs, setOrgs] = useState<Organization[]>([]);
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [, setLoading] = useState(false);

  const [organization, setOrganization] = useState(requestedOrg);
  const [branch, setBranch] = useState(requestedBranch);
  const [regNo, setRegNo] = useState("");
  const [vClass, setVClass] = useState("bus");
  const [make, setMake] = useState("");
  const [model, setModel] = useState("");
  const [seats, setSeats] = useState(33);
  const [creating, setCreating] = useState(false);

  const canSetup = useMemo(
    () => (user?.memberships ?? []).some((m) => m.is_active && ["operator_admin", "platform_admin"].includes(m.role)),
    [user]
  );
  const canView = useMemo(
    () =>
      (user?.memberships ?? []).some(
        (m) =>
          m.is_active &&
          ["operator_admin", "platform_admin", "branch_manager"].includes(m.role)
      ),
    [user]
  );

  const load = async () => {
    setLoading(true);
    try {
      const [o, v, b] = await Promise.all([
        listOrganizations(),
        listVehicles(),
        listBranches().catch(() => []),
      ]);
      setOrgs(o);
      setVehicles(v);
      setBranches(b);
      if (requestedOrg) {
        setOrganization(requestedOrg);
      } else if (!organization) {
        const m = user?.memberships.find((x) => x.is_active);
        setOrganization(m?.organization ?? o[0]?.id ?? "");
      }
    } catch (e: any) {
      toast({ title: "Could not load fleet", description: e?.message, type: "error" });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const branchOptions = useMemo(
    () => branches.filter((row) => row.organization === organization && row.is_active),
    [branches, organization]
  );

  useEffect(() => {
    if (branch && !branchOptions.some((row) => row.id === branch)) {
      setBranch("");
    }
  }, [branch, branchOptions]);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    setCreating(true);
    try {
      await createVehicle({
        organization,
        branch: branch || null,
        registration_number: regNo.toUpperCase(),
        vehicle_class: vClass,
        make,
        model,
        seating_capacity: seats,
        status: "active",
      });
      toast({ title: "Vehicle added", description: regNo.toUpperCase(), type: "success" });
      setRegNo("");
      setMake("");
      setModel("");
      await load();
    } catch (e: any) {
      toast({ title: "Failed to add vehicle", description: e?.response?.data?.detail || e?.message, type: "error" });
    } finally {
      setCreating(false);
    }
  }

  if (!canView) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-10 sm:px-6 lg:px-8">
        <Card>
          <CardHeader>
            <CardTitle>Fleet access restricted</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-[color:var(--text-subtle)]">
            Only branch managers, operator administrators and platform
            administrators can view fleet.
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
      <header>
        <p className="text-xs uppercase tracking-[0.18em] text-[color:var(--text-subtle)]">Fleet</p>
        <h1 className="text-2xl font-semibold tracking-tight text-[color:var(--text)]">Fleet setup</h1>
        <p className="mt-1 text-sm text-[color:var(--text-subtle)]">Add vehicles so you can assign them to departures.</p>
      </header>

      <div className="grid gap-6 lg:grid-cols-[1fr_1.2fr]">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Car className="h-4 w-4" />
              {canSetup ? "Add vehicle" : "Fleet access"}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {!canSetup ? (
              <p className="text-sm text-[color:var(--text-subtle)]">
                You can view and assign vehicles for your branch. Ask an operator
                administrator to create or transfer a vehicle.
              </p>
            ) : (
            <form onSubmit={onCreate} className="flex flex-col gap-3">
              <div className="space-y-2">
                <Label>Organization</Label>
                <Select value={organization} onChange={(e) => setOrganization(e.target.value)} required>
                  <option value="">Select</option>
                  {orgs.map((o) => (
                    <option key={o.id} value={o.id}>{o.name}</option>
                  ))}
                </Select>
              </div>
              <div className="space-y-2">
                <Label>Branch</Label>
                <Select value={branch} onChange={(e) => setBranch(e.target.value)}>
                  <option value="">HQ / shared pool</option>
                  {branchOptions.map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.name}
                    </option>
                  ))}
                </Select>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label>Registration number</Label>
                  <Input value={regNo} onChange={(e) => setRegNo(e.target.value)} placeholder="KDA 123X" required />
                </div>
                <div className="space-y-2">
                  <Label>Class</Label>
                  <Select value={vClass} onChange={(e) => setVClass(e.target.value)}>
                    {CLASSES.map((c) => (
                      <option key={c.value} value={c.value}>{c.label}</option>
                    ))}
                  </Select>
                </div>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label>Make</Label>
                  <Input value={make} onChange={(e) => setMake(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label>Model</Label>
                  <Input value={model} onChange={(e) => setModel(e.target.value)} />
                </div>
              </div>
              <div className="space-y-2">
                <Label>Seating capacity</Label>
                <Input type="number" min={1} value={seats} onChange={(e) => setSeats(Number(e.target.value))} />
              </div>
              <Button type="submit" disabled={creating || !organization} className="gap-2">
                <Plus className="h-4 w-4" />
                {creating ? "Adding..." : "Add vehicle"}
              </Button>
            </form>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <Users className="h-4 w-4" />
              Vehicles
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-auto rounded-xl border border-[color:var(--surface-border)]">
              <table className="w-full text-sm">
                <thead className="bg-[color:var(--surface-subtle)]">
                  <tr>
                    <th className="px-3 py-2 text-left font-medium">Reg no.</th>
                    <th className="px-3 py-2 text-left font-medium">Class</th>
                    <th className="px-3 py-2 text-left font-medium">Branch</th>
                    <th className="px-3 py-2 text-left font-medium">Make/model</th>
                    <th className="px-3 py-2 text-left font-medium">Seats</th>
                    <th className="px-3 py-2 text-left font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {vehicles.map((v) => (
                    <tr key={v.id} className="border-t border-[color:var(--surface-border)]">
                      <td className="px-3 py-2 font-medium">{v.registration_number}</td>
                      <td className="px-3 py-2 capitalize">{v.vehicle_class}</td>
                      <td className="px-3 py-2">{v.branch_name || "Shared pool"}</td>
                      <td className="px-3 py-2 text-[color:var(--text-subtle)]">{[v.make, v.model].filter(Boolean).join(" ") || "-"}</td>
                      <td className="px-3 py-2">{v.seating_capacity}</td>
                      <td className="px-3 py-2 capitalize">{v.status}</td>
                    </tr>
                  ))}
                  {vehicles.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="px-3 py-4 text-center text-[color:var(--text-subtle)]">No vehicles added yet.</td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
