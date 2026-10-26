import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Building2,
  Bus,
  Car,
  CheckCircle2,
  Circle,
  MapPin,
  ShieldCheck,
  Users,
} from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { describeError } from "@/api/client";
import {
  approveOrganization,
  getOrganization,
  listBranches,
  listRoutes,
  listVehicles,
  suspendOrganization,
  type Branch,
  type Organization,
  type Route,
  type Vehicle,
} from "@/api/setup";
import { listStaff } from "@/api/staff";
import type { StaffMember } from "@/api/types";
import { useAuth } from "@/context/AuthContext";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useToast } from "@/components/ui/toast";

const STATUS_VARIANT: Record<string, "default" | "success" | "warning" | "danger"> = {
  pending: "warning",
  approved: "success",
  rejected: "danger",
  suspended: "danger",
};

const STATUS_LABEL: Record<string, string> = {
  pending: "Pending approval",
  approved: "Approved",
  rejected: "Rejected",
  suspended: "Suspended",
};

interface Step {
  key: string;
  title: string;
  description: string;
  done: boolean;
  to: string;
  cta: string;
  icon: React.ReactNode;
}

export function OrganizationDetailPage() {
  const { id = "" } = useParams();
  const { user } = useAuth();
  const { toast } = useToast();

  const [org, setOrg] = useState<Organization | null>(null);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [routes, setRoutes] = useState<Route[]>([]);
  const [staff, setStaff] = useState<StaffMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);

  const isPlatformAdmin = useMemo(
    () =>
      (user?.memberships ?? []).some(
        (m) => m.is_active && m.role === "platform_admin"
      ),
    [user]
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [organization, allBranches, allVehicles, allRoutes, members] =
        await Promise.all([
          getOrganization(id),
          listBranches({ organization: id }),
          listVehicles(),
          listRoutes(),
          isPlatformAdmin ? listStaff({ organization: id }) : Promise.resolve([]),
        ]);
      setOrg(organization);
      setBranches(allBranches);
      setVehicles(allVehicles.filter((v) => v.organization === id));
      setRoutes(allRoutes.filter((r) => r.organization === id));
      setStaff(members);
    } catch (error) {
      toast({
        title: "Could not load organization",
        description: describeError(error),
        type: "error",
      });
    } finally {
      setLoading(false);
    }
  }, [id, isPlatformAdmin, toast]);

  useEffect(() => {
    void load();
  }, [load]);

  const activeStaff = staff.filter((m) => m.is_active);
  const hasAdmin = activeStaff.some((m) =>
    ["operator_admin", "platform_admin"].includes(m.role)
  );

  const steps: Step[] = [
    {
      key: "profile",
      title: "Company profile",
      description: "Name, slug and operating currency are set.",
      done: Boolean(org?.name && org?.slug),
      to: `/setup/company?org=${id}`,
      cta: "Edit profile",
      icon: <Building2 className="h-4 w-4" />,
    },
    {
      key: "branches",
      title: "Branches",
      description: `${branches.length} branch${branches.length === 1 ? "" : "es"} configured.`,
      done: branches.length > 0,
      to: `/branches?org=${id}`,
      cta: branches.length > 0 ? "Manage branches" : "Add a branch",
      icon: <MapPin className="h-4 w-4" />,
    },
    {
      key: "fleet",
      title: "Fleet",
      description: `${vehicles.length} vehicle${vehicles.length === 1 ? "" : "s"} registered.`,
      done: vehicles.length > 0,
      to: `/setup/fleet?org=${id}`,
      cta: vehicles.length > 0 ? "Manage fleet" : "Add a vehicle",
      icon: <Car className="h-4 w-4" />,
    },
    {
      key: "routes",
      title: "Routes & departures",
      description: `${routes.length} route${routes.length === 1 ? "" : "s"} configured.`,
      done: routes.length > 0,
      to: `/setup/routes?org=${id}`,
      cta: routes.length > 0 ? "Manage routes" : "Create a route",
      icon: <Bus className="h-4 w-4" />,
    },
    {
      key: "team",
      title: "Team",
      description: hasAdmin
        ? `${activeStaff.length} active staff, with an administrator.`
        : `${activeStaff.length} active staff — assign an operator admin.`,
      done: hasAdmin,
      to: `/people?org=${id}`,
      cta: hasAdmin ? "Manage team" : "Hire an operator admin",
      icon: <Users className="h-4 w-4" />,
    },
  ];

  const completed = steps.filter((s) => s.done).length;
  const progress = Math.round((completed / steps.length) * 100);
  const status = org?.approval_status ?? "pending";

  async function onApprove() {
    setWorking(true);
    try {
      const updated = await approveOrganization(id);
      setOrg(updated);
      toast({ title: "Organization approved", description: updated.name, type: "success" });
    } catch (error) {
      toast({ title: "Could not approve", description: describeError(error), type: "error" });
    } finally {
      setWorking(false);
    }
  }

  async function onSuspend() {
    setWorking(true);
    try {
      const updated = await suspendOrganization(id);
      setOrg(updated);
      toast({ title: "Organization suspended", description: updated.name, type: "success" });
    } catch (error) {
      toast({ title: "Could not suspend", description: describeError(error), type: "error" });
    } finally {
      setWorking(false);
    }
  }

  if (loading) {
    return (
      <div className="mx-auto max-w-5xl px-4 py-10 text-sm text-[color:var(--text-subtle)]">
        Loading organization…
      </div>
    );
  }

  if (!org) {
    return (
      <div className="mx-auto max-w-5xl px-4 py-10">
        <p className="text-sm text-[color:var(--text-subtle)]">
          This organization could not be found.
        </p>
        <Link
          to="/setup/company"
          className="mt-4 inline-flex items-center gap-2 text-sm font-medium text-[color:var(--primary)]"
        >
          <ArrowLeft className="h-4 w-4" /> Back to company setup
        </Link>
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
      <div>
        <Link
          to="/setup/company"
          className="inline-flex items-center gap-2 text-sm text-[color:var(--text-subtle)] transition-colors hover:text-[color:var(--text)]"
        >
          <ArrowLeft className="h-4 w-4" /> Company setup
        </Link>
        <div className="mt-3 flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-[color:var(--primary)]/10 text-[color:var(--primary)]">
              <Building2 className="h-6 w-6" />
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="text-2xl font-semibold tracking-tight text-[color:var(--text)]">
                  {org.name}
                </h1>
                <Badge variant={STATUS_VARIANT[status] ?? "default"}>
                  {STATUS_LABEL[status] ?? status}
                </Badge>
              </div>
              <p className="mt-1 text-sm text-[color:var(--text-subtle)]">
                {org.slug} · {org.org_type ?? "carrier"} · {org.currency ?? "KES"} ·{" "}
                {org.timezone ?? "Africa/Nairobi"}
              </p>
            </div>
          </div>
          {isPlatformAdmin && (
            <div className="flex flex-wrap gap-2">
              {status !== "approved" && (
                <Button
                  onClick={() => void onApprove()}
                  loading={working}
                  disabled={working}
                  icon={<ShieldCheck className="h-4 w-4" />}
                >
                  Approve
                </Button>
              )}
              {status === "approved" && (
                <Button
                  variant="danger"
                  onClick={() => void onSuspend()}
                  loading={working}
                  disabled={working}
                >
                  Suspend
                </Button>
              )}
            </div>
          )}
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Onboarding progress</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex items-center justify-between text-sm">
            <span className="text-[color:var(--text-subtle)]">
              {completed} of {steps.length} steps complete
            </span>
            <span className="font-semibold text-[color:var(--text)]">{progress}%</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-[color:var(--surface-muted)]">
            <div
              className="h-full rounded-full bg-[color:var(--primary)] transition-all"
              style={{ width: `${progress}%` }}
            />
          </div>

          <ol className="mt-2 flex flex-col divide-y divide-[color:var(--surface-border)]">
            {steps.map((step) => (
              <li key={step.key} className="flex flex-wrap items-center gap-3 py-3">
                <span
                  className={
                    step.done
                      ? "flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[color:var(--success-bg)] text-[color:var(--success)]"
                      : "flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[color:var(--surface-muted)] text-[color:var(--text-subtle)]"
                  }
                >
                  {step.done ? (
                    <CheckCircle2 className="h-5 w-5" />
                  ) : (
                    <Circle className="h-4 w-4" />
                  )}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="flex items-center gap-2 font-medium text-[color:var(--text)]">
                    {step.icon}
                    {step.title}
                  </p>
                  <p className="text-sm text-[color:var(--text-subtle)]">
                    {step.description}
                  </p>
                </div>
                <Link
                  to={step.to}
                  className="inline-flex items-center gap-1 rounded-md border border-[color:var(--surface-border)] px-3 py-1.5 text-sm font-medium text-[color:var(--text)] transition-colors hover:bg-[color:var(--surface-muted)]"
                >
                  {step.cta}
                  <ArrowRight className="h-3.5 w-3.5" />
                </Link>
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Organization details</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <Detail label="Legal name" value={org.name} />
          <Detail label="Slug" value={org.slug} />
          <Detail label="Type" value={org.org_type ?? "carrier"} />
          <Detail label="Currency" value={org.currency ?? "KES"} />
          <Detail label="Contact phone" value={org.contact_phone || "Not set"} />
          <Detail label="Address" value={org.address || "Not set"} />
        </CardContent>
      </Card>
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs uppercase tracking-[0.14em] text-[color:var(--text-subtle)]">
        {label}
      </p>
      <p className="mt-1 text-sm text-[color:var(--text)]">{value}</p>
    </div>
  );
}
