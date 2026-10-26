import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import {
  ArrowLeft,
  Bus,
  Car,
  MapPin,
  Phone,
  Save,
  Star,
  Users,
  Warehouse,
} from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { describeError } from "@/api/client";
import {
  getBranch,
  listDepartures,
  listRoutes,
  listVehicles,
  updateBranch,
  type Branch,
} from "@/api/setup";
import { listStaff } from "@/api/staff";
import { useAuth } from "@/context/AuthContext";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/toast";

export function BranchDetailPage() {
  const { id = "" } = useParams();
  const { user } = useAuth();
  const { toast } = useToast();

  const [branch, setBranch] = useState<Branch | null>(null);
  const [counts, setCounts] = useState({
    staff: 0,
    vehicles: 0,
    routes: 0,
    departures: 0,
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [address, setAddress] = useState("");
  const [phone, setPhone] = useState("");
  const [headOffice, setHeadOffice] = useState(false);
  const [active, setActive] = useState(true);

  const canManage = useMemo(() => {
    if (!branch) return false;
    return (user?.memberships ?? []).some(
      (m) =>
        m.is_active &&
        ((m.role === "platform_admin") ||
          (m.role === "operator_admin" && m.organization === branch.organization))
    );
  }, [branch, user]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const row = await getBranch(id);
      setBranch(row);
      setName(row.name);
      setCode(row.code);
      setAddress(row.address);
      setPhone(row.contact_phone);
      setHeadOffice(row.is_head_office);
      setActive(row.is_active);
      const [staff, vehicles, routes, departures] = await Promise.all([
        listStaff({ branch: id, includeInactive: false }).catch(() => []),
        listVehicles({ branch: id }).catch(() => []),
        listRoutes({ branch: id }).catch(() => []),
        listDepartures({ branch: id }).catch(() => []),
      ]);
      setCounts({
        staff: staff.filter((member) => member.is_active).length,
        vehicles: vehicles.length,
        routes: routes.length,
        departures: departures.length,
      });
    } catch (error) {
      toast({
        title: "Could not load branch",
        description: describeError(error),
        type: "error",
      });
    } finally {
      setLoading(false);
    }
  }, [id, toast]);

  useEffect(() => {
    void load();
  }, [load]);

  async function onSave(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    try {
      const updated = await updateBranch(id, {
        name: name.trim(),
        code: code.trim().toUpperCase(),
        address: address.trim(),
        contact_phone: phone.trim(),
        is_head_office: headOffice,
        is_active: active,
      });
      setBranch(updated);
      toast({
        title: "Branch updated",
        description: `${updated.name} saved.`,
        type: "success",
      });
    } catch (error) {
      toast({
        title: "Could not update branch",
        description: describeError(error),
        type: "error",
      });
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="mx-auto max-w-5xl px-4 py-10 text-sm text-[var(--text-secondary)]">
        Loading branch…
      </div>
    );
  }

  if (!branch) {
    return (
      <div className="mx-auto max-w-5xl px-4 py-10">
        <p className="text-sm text-[var(--text-secondary)]">Branch not found.</p>
        <Link
          to="/branches"
          className="mt-3 inline-flex items-center gap-2 text-sm font-medium text-[color:var(--primary)]"
        >
          <ArrowLeft className="h-4 w-4" /> Branches
        </Link>
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
      <div>
        <Link
          to="/branches"
          className="inline-flex items-center gap-2 text-sm text-[var(--text-secondary)] transition hover:text-[var(--text-primary)]"
        >
          <ArrowLeft className="h-4 w-4" /> Branches
        </Link>
        <div className="mt-3 flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-[color:var(--primary)]/10 text-[color:var(--primary)]">
              <Warehouse className="h-6 w-6" />
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="text-2xl font-semibold tracking-tight text-[var(--text-primary)]">
                  {branch.name}
                </h1>
                {branch.code ? <Badge variant="default">{branch.code}</Badge> : null}
                {branch.is_head_office ? (
                  <Badge variant="success" className="gap-1">
                    <Star className="h-3 w-3" /> Head office
                  </Badge>
                ) : null}
                {!branch.is_active ? <Badge variant="danger">Inactive</Badge> : null}
              </div>
              <p className="mt-1 text-sm text-[var(--text-secondary)]">
                {branch.organization_name}
              </p>
            </div>
          </div>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-4">
        <Stat icon={<Users className="h-4 w-4" />} label="Active staff" value={counts.staff} />
        <Stat icon={<Car className="h-4 w-4" />} label="Vehicles" value={counts.vehicles} />
        <Stat icon={<Bus className="h-4 w-4" />} label="Routes" value={counts.routes} />
        <Stat icon={<Warehouse className="h-4 w-4" />} label="Departures" value={counts.departures} />
      </div>

      <div className="grid gap-6 lg:grid-cols-[1.1fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Branch details</CardTitle>
          </CardHeader>
          <CardContent>
            {canManage ? (
              <form onSubmit={onSave} className="flex flex-col gap-3">
                <div className="space-y-2">
                  <Label>Name</Label>
                  <Input value={name} onChange={(event) => setName(event.target.value)} required />
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label>Code</Label>
                    <Input
                      value={code}
                      onChange={(event) => setCode(event.target.value)}
                      maxLength={16}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>Phone</Label>
                    <Input value={phone} onChange={(event) => setPhone(event.target.value)} />
                  </div>
                </div>
                <div className="space-y-2">
                  <Label>Address</Label>
                  <Input value={address} onChange={(event) => setAddress(event.target.value)} />
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="flex items-start gap-2 rounded-xl border border-[var(--border)] p-3 text-sm">
                    <input
                      type="checkbox"
                      checked={headOffice}
                      onChange={(event) => setHeadOffice(event.target.checked)}
                      className="mt-0.5 h-4 w-4 accent-[color:var(--primary)]"
                    />
                    <span>
                      <span className="block font-medium text-[var(--text-primary)]">
                        Head office
                      </span>
                      <span className="text-xs text-[var(--text-secondary)]">
                        One per organisation.
                      </span>
                    </span>
                  </label>
                  <label className="flex items-start gap-2 rounded-xl border border-[var(--border)] p-3 text-sm">
                    <input
                      type="checkbox"
                      checked={active}
                      onChange={(event) => setActive(event.target.checked)}
                      className="mt-0.5 h-4 w-4 accent-[color:var(--primary)]"
                    />
                    <span>
                      <span className="block font-medium text-[var(--text-primary)]">
                        Active
                      </span>
                      <span className="text-xs text-[var(--text-secondary)]">
                        Inactive branches cannot sell or dispatch.
                      </span>
                    </span>
                  </label>
                </div>
                <Button type="submit" disabled={saving} className="gap-2">
                  <Save className="h-4 w-4" />
                  {saving ? "Saving…" : "Save changes"}
                </Button>
              </form>
            ) : (
              <dl className="space-y-3 text-sm">
                <Detail icon={<MapPin className="h-4 w-4" />} label="Address" value={branch.address || "Not set"} />
                <Detail icon={<Phone className="h-4 w-4" />} label="Phone" value={branch.contact_phone || "Not set"} />
                <Detail icon={<Star className="h-4 w-4" />} label="Head office" value={branch.is_head_office ? "Yes" : "No"} />
                <Detail icon={<Warehouse className="h-4 w-4" />} label="Status" value={branch.is_active ? "Active" : "Inactive"} />
              </dl>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Operating scope</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm text-[var(--text-secondary)]">
            <p>
              Tickets sold here are attributed to this branch even when the departure
              belongs to another branch. Boarding and manifests stay with the branch
              that operates the departure.
            </p>
            <div className="rounded-xl border border-[var(--border)] bg-[var(--surface-subtle)] p-3">
              <p className="text-xs uppercase tracking-[0.14em] text-[var(--text-tertiary)]">
                Selling branch
              </p>
              <p className="mt-1 font-medium text-[var(--text-primary)]">{branch.name}</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Link
                to={`/counter?branch=${branch.id}`}
                className="inline-flex items-center gap-2 rounded-lg border border-[var(--border)] px-3 py-2 text-sm font-medium transition hover:border-[color:var(--primary)]/50"
              >
                <Bus className="h-4 w-4" /> Counter sales
              </Link>
              <Link
                to={`/people?org=${branch.organization}`}
                className="inline-flex items-center gap-2 rounded-lg border border-[var(--border)] px-3 py-2 text-sm font-medium transition hover:border-[color:var(--primary)]/50"
              >
                <Users className="h-4 w-4" /> Team
              </Link>
              <Link
                to={`/setup/fleet?org=${branch.organization}&branch=${branch.id}`}
                className="inline-flex items-center gap-2 rounded-lg border border-[var(--border)] px-3 py-2 text-sm font-medium transition hover:border-[color:var(--primary)]/50"
              >
                <Car className="h-4 w-4" /> Fleet
              </Link>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function Stat({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: number;
}) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4 shadow-[var(--shadow-sm)]">
      <div className="flex items-center gap-2 text-[var(--text-tertiary)]">
        {icon}
        <span className="text-[11px] uppercase tracking-[0.14em]">{label}</span>
      </div>
      <p className="mt-1 text-xl font-semibold text-[var(--text-primary)]">{value}</p>
    </div>
  );
}

function Detail({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-start gap-3">
      <span className="mt-0.5 text-[var(--text-tertiary)]">{icon}</span>
      <div>
        <dt className="text-[11px] uppercase tracking-[0.14em] text-[var(--text-tertiary)]">
          {label}
        </dt>
        <dd className="font-medium text-[var(--text-primary)]">{value}</dd>
      </div>
    </div>
  );
}
