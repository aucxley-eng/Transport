import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  ArrowRight,
  Building2,
  MapPin,
  Phone,
  Plus,
  Star,
  Warehouse,
} from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";

import { describeError } from "@/api/client";
import {
  createBranch,
  listBranches,
  listOrganizations,
  type Branch,
  type Organization,
} from "@/api/setup";
import { useAuth } from "@/context/AuthContext";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { useToast } from "@/components/ui/toast";

export function BranchesPage() {
  const { user } = useAuth();
  const { toast } = useToast();
  const [searchParams] = useSearchParams();
  const requestedOrg = searchParams.get("org") ?? "";

  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [organizationId, setOrganizationId] = useState(requestedOrg);
  const [loading, setLoading] = useState(true);

  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [address, setAddress] = useState("");
  const [phone, setPhone] = useState("");
  const [headOffice, setHeadOffice] = useState(true);
  const [creating, setCreating] = useState(false);

  const isPlatformAdmin = (user?.memberships ?? []).some(
    (m) => m.is_active && m.role === "platform_admin"
  );
  const canManage = useMemo(
    () =>
      isPlatformAdmin ||
      (user?.memberships ?? []).some(
        (m) =>
          m.is_active &&
          m.role === "operator_admin" &&
          m.organization === organizationId
      ),
    [isPlatformAdmin, organizationId, user]
  );

  const visible = useMemo(
    () => branches.filter((branch) => branch.organization === organizationId),
    [branches, organizationId]
  );
  const activeCount = visible.filter((branch) => branch.is_active).length;
  const hasHeadOffice = visible.some(
    (branch) => branch.is_active && branch.is_head_office
  );

  useEffect(() => {
    let cancelled = false;
    Promise.all([listOrganizations(), listBranches()])
      .then(([orgs, rows]) => {
        if (cancelled) return;
        setOrganizations(orgs);
        setBranches(rows);
        const membershipOrg = user?.memberships.find((m) => m.is_active)?.organization;
        const preferred =
          requestedOrg ||
          orgs.find((org) => org.id === membershipOrg)?.id ||
          orgs.find((org) => org.org_type !== "platform" && org.approval_status === "approved")?.id ||
          orgs[0]?.id ||
          "";
        setOrganizationId((current) => current || preferred);
      })
      .catch((error) =>
        toast({
          title: "Could not load branches",
          description: describeError(error),
          type: "error",
        })
      )
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [requestedOrg, toast, user]);

  useEffect(() => {
    setHeadOffice(!visible.some((branch) => branch.is_active && branch.is_head_office));
  }, [organizationId, visible.length]);

  async function reload() {
    try {
      setBranches(await listBranches());
    } catch (error) {
      toast({
        title: "Could not refresh branches",
        description: describeError(error),
        type: "error",
      });
    }
  }

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    if (!organizationId) return;
    setCreating(true);
    try {
      const branch = await createBranch({
        organization: organizationId,
        name: name.trim(),
        code: code.trim().toUpperCase(),
        address: address.trim(),
        contact_phone: phone.trim(),
        is_head_office: headOffice,
      });
      toast({
        title: "Branch added",
        description: `${branch.name} is ready for staff, fleet and departures.`,
        type: "success",
      });
      setName("");
      setCode("");
      setAddress("");
      setPhone("");
      await reload();
    } catch (error) {
      toast({
        title: "Could not add branch",
        description: describeError(error),
        type: "error",
      });
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-xs uppercase tracking-[0.18em] text-[color:var(--text-tertiary)]">
            Network
          </p>
          <h1 className="text-2xl font-semibold tracking-tight text-[color:var(--text-primary)]">
            Branches
          </h1>
          <p className="mt-1 text-sm text-[color:var(--text-secondary)]">
            Operating locations, their teams and the departures they run.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Select
            value={organizationId}
            onChange={(event) => setOrganizationId(event.target.value)}
            aria-label="Organisation"
            className="min-w-[220px]"
          >
            {organizations.map((org) => (
              <option key={org.id} value={org.id}>
                {org.name}
              </option>
            ))}
          </Select>
        </div>
      </header>

      <div className="grid gap-3 sm:grid-cols-3">
        <Metric label="Branches" value={visible.length} hint={`${activeCount} active`} />
        <Metric
          label="Head office"
          value={hasHeadOffice ? "Set" : "Required"}
          hint={hasHeadOffice ? "One branch leads the network" : "Promote a branch"}
        />
        <Metric
          label="Coverage"
          value={new Set(visible.map((branch) => branch.code || branch.name)).size}
          hint="Unique branch codes"
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Warehouse className="h-4 w-4" />
              Branch network
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {loading ? (
              <p className="text-sm text-[color:var(--text-secondary)]">Loading branches…</p>
            ) : visible.length === 0 ? (
              <div className="rounded-xl border border-dashed border-[var(--border)] px-4 py-10 text-center">
                <Building2 className="mx-auto h-8 w-8 text-[var(--text-tertiary)]" />
                <p className="mt-2 text-sm font-medium text-[var(--text-primary)]">
                  No branches yet
                </p>
                <p className="mt-1 text-xs text-[var(--text-secondary)]">
                  Add the first operating location to start assigning staff and departures.
                </p>
              </div>
            ) : (
              visible.map((branch) => (
                <Link
                  key={branch.id}
                  to={`/branches/${branch.id}`}
                  className="group flex items-center justify-between gap-4 rounded-xl border border-[var(--border)] bg-[var(--surface)] px-4 py-3 transition hover:border-[color:var(--primary)]/50 hover:shadow-[var(--shadow-sm)]"
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="truncate text-sm font-semibold text-[var(--text-primary)]">
                        {branch.name}
                      </p>
                      {branch.code ? <Badge variant="default">{branch.code}</Badge> : null}
                      {branch.is_head_office ? (
                        <Badge variant="success" className="gap-1">
                          <Star className="h-3 w-3" /> Head office
                        </Badge>
                      ) : null}
                      {!branch.is_active ? <Badge variant="danger">Inactive</Badge> : null}
                    </div>
                    <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--text-secondary)]">
                      {branch.address ? (
                        <span className="inline-flex items-center gap-1">
                          <MapPin className="h-3 w-3" /> {branch.address}
                        </span>
                      ) : null}
                      {branch.contact_phone ? (
                        <span className="inline-flex items-center gap-1">
                          <Phone className="h-3 w-3" /> {branch.contact_phone}
                        </span>
                      ) : null}
                    </div>
                  </div>
                  <ArrowRight className="h-4 w-4 shrink-0 text-[var(--text-tertiary)] transition group-hover:translate-x-0.5 group-hover:text-[color:var(--primary)]" />
                </Link>
              ))
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Plus className="h-4 w-4" />
              Add branch
            </CardTitle>
          </CardHeader>
          <CardContent>
            {canManage ? (
              <form onSubmit={onCreate} className="flex flex-col gap-3">
                <div className="space-y-2">
                  <Label>Branch name</Label>
                  <Input
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    placeholder="Nairobi CBD"
                    required
                  />
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label>Code</Label>
                    <Input
                      value={code}
                      onChange={(event) => setCode(event.target.value)}
                      placeholder="NBO"
                      maxLength={16}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>Phone</Label>
                    <Input
                      value={phone}
                      onChange={(event) => setPhone(event.target.value)}
                      placeholder="0712 345 678"
                    />
                  </div>
                </div>
                <div className="space-y-2">
                  <Label>Address</Label>
                  <Input
                    value={address}
                    onChange={(event) => setAddress(event.target.value)}
                    placeholder="Crossroads, Mombasa Road"
                  />
                </div>
                <label className="flex items-start gap-3 rounded-xl border border-[var(--border)] bg-[var(--surface-subtle)] p-3 text-sm">
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
                      Required once the network has more than one active branch.
                    </span>
                  </span>
                </label>
                <Button
                  type="submit"
                  disabled={creating || !organizationId}
                  className="gap-2"
                >
                  <Plus className="h-4 w-4" />
                  {creating ? "Adding…" : "Add branch"}
                </Button>
              </form>
            ) : (
              <p className="text-sm text-[color:var(--text-secondary)]">
                Only operator or platform administrators can add branches. Ask an
                administrator to open this branch for you.
              </p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | number;
  hint: string;
}) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4 shadow-[var(--shadow-sm)]">
      <p className="text-[11px] uppercase tracking-[0.16em] text-[var(--text-tertiary)]">
        {label}
      </p>
      <p className="mt-1 text-xl font-semibold text-[var(--text-primary)]">{value}</p>
      <p className="text-xs text-[var(--text-secondary)]">{hint}</p>
    </div>
  );
}
