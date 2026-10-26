import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Briefcase,
  Building2,
  ChevronDown,
  Mail,
  MapPin,
  Phone,
  Plus,
  Search,
  UserPlus,
  Users,
  UserX,
} from "lucide-react";

import { describeError } from "@/api/client";
import {
  hireStaff,
  listBranches,
  listMyOrganizations,
  listStaff,
  offboardStaff,
  updateStaff,
} from "@/api/staff";
import type {
  Branch,
  Organization,
  Role,
  StaffHireInput,
  StaffMember,
} from "@/api/types";
import { ROLE_LABELS, STAFF_ROLES } from "@/api/types";
import { useAuth } from "@/context/AuthContext";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Modal } from "@/components/ui/modal";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { cn } from "@/lib/utils";

const MANAGE_ROLES: Role[] = ["operator_admin", "platform_admin"];

function initials(name: string, phone: string): string {
  const source = name.trim() || phone;
  const parts = source.split(/\s+/).filter(Boolean);
  const first = parts[0]?.charAt(0) ?? "";
  if (parts.length === 1) return (parts[0] ?? source).slice(0, 2).toUpperCase();
  const last = parts[parts.length - 1]?.charAt(0) ?? "";
  return `${first}${last}`.toUpperCase();
}

function formatDate(value: string | null): string {
  if (!value) return "Not recorded";
  return new Date(`${value}T00:00:00`).toLocaleDateString("en-KE", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function roleBadge(role: Role) {
  if (MANAGE_ROLES.includes(role)) return "info" as const;
  if (role === "branch_manager") return "warning" as const;
  if (role === "finance_officer") return "success" as const;
  return "default" as const;
}

interface HireForm {
  full_name: string;
  phone: string;
  email: string;
  password: string;
  role: Role;
  branch: string;
  job_title: string;
  employee_number: string;
  hired_at: string;
}

const EMPTY_HIRE: HireForm = {
  full_name: "",
  phone: "",
  email: "",
  password: "",
  role: "branch_staff",
  branch: "",
  job_title: "",
  employee_number: "",
  hired_at: new Date().toISOString().slice(0, 10),
};

export function StaffPage() {
  const { user } = useAuth();
  const { toast } = useToast();
  const [searchParams] = useSearchParams();
  const requestedOrg = searchParams.get("org") ?? "";

  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [organizationId, setOrganizationId] = useState(requestedOrg);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [staff, setStaff] = useState<StaffMember[]>([]);

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [branchFilter, setBranchFilter] = useState("");
  const [includeInactive, setIncludeInactive] = useState(false);

  const [hireOpen, setHireOpen] = useState(false);
  const [hireForm, setHireForm] = useState<HireForm>(EMPTY_HIRE);
  const [hireError, setHireError] = useState<string | null>(null);
  const [hiring, setHiring] = useState(false);

  const [editing, setEditing] = useState<StaffMember | null>(null);
  const [editForm, setEditForm] = useState<HireForm>(EMPTY_HIRE);
  const [savingEdit, setSavingEdit] = useState(false);

  const [offboarding, setOffboarding] = useState<StaffMember | null>(null);
  const [offboardingBusy, setOffboardingBusy] = useState(false);

  const adminMemberships = useMemo(
    () =>
      (user?.memberships ?? []).filter(
        (m) => m.is_active && MANAGE_ROLES.includes(m.role)
      ),
    [user]
  );
  const managerMemberships = useMemo(
    () =>
      (user?.memberships ?? []).filter(
        (m) => m.is_active && m.role === "branch_manager"
      ),
    [user]
  );
  const isPlatformAdmin = (user?.memberships ?? []).some(
    (m) => m.is_active && m.role === "platform_admin"
  );
  const canManage =
    isPlatformAdmin || adminMemberships.length > 0 || managerMemberships.length > 0;

  const selectedOrganization = organizations.find((o) => o.id === organizationId);
  const isOrgAdmin =
    isPlatformAdmin ||
    adminMemberships.some((m) => m.organization === organizationId);
  const managerBranch = managerMemberships.find(
    (m) => m.organization === organizationId
  );
  const canHireHere = isOrgAdmin || Boolean(managerBranch);
  const assignableRoles: Role[] = isOrgAdmin ? STAFF_ROLES : ["branch_staff"];

  useEffect(() => {
    let cancelled = false;
    listMyOrganizations()
      .then((orgs) => {
        if (cancelled) return;
        setOrganizations(orgs);
        const adminOrg = adminMemberships[0]?.organization;
        const managerOrg = managerMemberships[0]?.organization;
        const preferred =
          orgs.find((o) => o.id === adminOrg && o.org_type !== "platform")?.id ??
          orgs.find((o) => o.org_type !== "platform")?.id ??
          orgs.find((o) => o.id === managerOrg)?.id ??
          orgs[0]?.id ??
          "";
        setOrganizationId((current) => current || preferred);
      })
      .catch((error) => {
        if (!cancelled) setLoadError(describeError(error));
      });
    return () => {
      cancelled = true;
    };
  }, [adminMemberships, managerMemberships]);

  const loadBranchesFor = useCallback(
    async (orgId: string) => {
      if (!orgId) {
        setBranches([]);
        return;
      }
      try {
        setBranches(await listBranches(orgId));
      } catch {
        setBranches([]);
      }
    },
    []
  );

  const loadStaffFor = useCallback(
    async (orgId: string, showSpinner = true) => {
      if (!orgId) {
        setStaff([]);
        setLoading(false);
        return;
      }
      if (showSpinner) setLoading(true);
      setLoadError(null);
      try {
        const rows = await listStaff({
          organization: orgId,
          includeInactive,
        });
        setStaff(rows);
      } catch (error) {
        setLoadError(describeError(error));
      } finally {
        setLoading(false);
      }
    },
    [includeInactive]
  );

  useEffect(() => {
    if (!organizationId) return;
    void loadBranchesFor(organizationId);
    void loadStaffFor(organizationId);
  }, [organizationId, loadBranchesFor, loadStaffFor]);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return staff.filter((member) => {
      if (roleFilter && member.role !== roleFilter) return false;
      if (branchFilter && member.branch !== branchFilter) return false;
      if (!needle) return true;
      return [
        member.full_name,
        member.phone,
        member.job_title,
        member.employee_number,
        member.role_label,
        member.branch_name ?? "",
      ]
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
  }, [staff, search, roleFilter, branchFilter]);

  const stats = useMemo(() => {
    const active = staff.filter((s) => s.is_active).length;
    const branches = new Set(
      staff.filter((s) => s.is_active && s.branch).map((s) => s.branch)
    ).size;
    const roles = new Set(staff.filter((s) => s.is_active).map((s) => s.role)).size;
    return { total: staff.length, active, branches, roles };
  }, [staff]);

  function openHire() {
    setHireForm({
      ...EMPTY_HIRE,
      branch: managerBranch?.branch ?? branches[0]?.id ?? "",
      role: isOrgAdmin ? "branch_staff" : "branch_staff",
    });
    setHireError(null);
    setHireOpen(true);
  }

  async function submitHire(event: FormEvent) {
    event.preventDefault();
    setHireError(null);
    if (!organizationId) {
      setHireError("Select an organisation first.");
      return;
    }
    if (!hireForm.full_name.trim()) {
      setHireError("Enter the person's full name.");
      return;
    }
    if (hireForm.password.length > 0 && hireForm.password.length < 8) {
      setHireError("A password must be at least 8 characters.");
      return;
    }

    const input: StaffHireInput = {
      organization: organizationId,
      phone: hireForm.phone,
      full_name: hireForm.full_name.trim(),
      role: hireForm.role,
      branch: hireForm.branch || null,
      job_title: hireForm.job_title.trim(),
      employee_number: hireForm.employee_number.trim(),
      hired_at: hireForm.hired_at || null,
    };
    if (hireForm.email.trim()) input.email = hireForm.email.trim();
    if (hireForm.password) input.password = hireForm.password;

    setHiring(true);
    try {
      const member = await hireStaff(input);
      toast({
        type: "success",
        title: "Staff member added",
        description: `${member.full_name || member.phone} can now sign in as ${member.role_label}.`,
      });
      setHireOpen(false);
      setHireForm(EMPTY_HIRE);
      await loadStaffFor(organizationId, false);
    } catch (error) {
      setHireError(describeError(error));
    } finally {
      setHiring(false);
    }
  }

  function openEdit(member: StaffMember) {
    setEditing(member);
    setEditForm({
      full_name: member.full_name,
      phone: member.phone,
      email: member.email ?? "",
      password: "",
      role: member.role,
      branch: member.branch ?? "",
      job_title: member.job_title,
      employee_number: member.employee_number,
      hired_at: member.hired_at ?? "",
    });
  }

  async function submitEdit(event: FormEvent) {
    event.preventDefault();
    if (!editing) return;
    setSavingEdit(true);
    try {
      const updated = await updateStaff(editing.id, {
        role: editForm.role,
        branch: editForm.branch || null,
        job_title: editForm.job_title.trim(),
        employee_number: editForm.employee_number.trim(),
        hired_at: editForm.hired_at || null,
        is_active: editing.is_active,
      });
      toast({
        type: "success",
        title: "Staff record updated",
        description: `${updated.full_name || updated.phone} is now ${updated.role_label}.`,
      });
      setEditing(null);
      await loadStaffFor(organizationId, false);
    } catch (error) {
      toast({ type: "error", description: describeError(error) });
    } finally {
      setSavingEdit(false);
    }
  }

  async function confirmOffboard() {
    if (!offboarding) return;
    setOffboardingBusy(true);
    try {
      await offboardStaff(offboarding.id);
      toast({
        type: "success",
        title: "Access revoked",
        description: `${offboarding.full_name || offboarding.phone} can no longer sign in. History is retained.`,
      });
      setOffboarding(null);
      await loadStaffFor(organizationId, false);
    } catch (error) {
      toast({ type: "error", description: describeError(error) });
    } finally {
      setOffboardingBusy(false);
    }
  }

  return (
    <div className="content">
      <div className="flex flex-col gap-6">
        <header className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="mb-1 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
              <Users className="h-3.5 w-3.5" />
              People and access
            </div>
            <h1 className="text-2xl font-bold tracking-tight">Staff directory</h1>
            <p className="mt-1 max-w-2xl text-sm text-[var(--text-tertiary)]">
              Hire the people who run the counter, dispatch desk and fleet. Each person
              gets their own sign-in and a role scoped to one organisation.
            </p>
          </div>
          {canManage && (
            <Button
              icon={<UserPlus className="h-4 w-4" />}
              onClick={openHire}
              disabled={!canHireHere || organizations.length === 0}
              title={
                canHireHere
                  ? undefined
                  : "You do not administer this organisation"
              }
            >
              Hire staff
            </Button>
          )}
        </header>

        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {[
            { label: "People on record", value: stats.total, icon: Users },
            { label: "Active accounts", value: stats.active, icon: Briefcase },
            { label: "Branches covered", value: stats.branches, icon: Building2 },
            { label: "Distinct roles", value: stats.roles, icon: UserPlus },
          ].map((card) => (
            <div key={card.label} className="card !p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-tertiary)]">
                    {card.label}
                  </div>
                  <div className="mt-1 text-2xl font-bold tabular-nums">
                    {loading ? <Skeleton className="h-7 w-10" /> : card.value}
                  </div>
                </div>
                <div className="grid h-9 w-9 place-items-center rounded-lg bg-[var(--surface-subtle)] text-[var(--text-tertiary)]">
                  <card.icon className="h-4 w-4" />
                </div>
              </div>
            </div>
          ))}
        </div>

        <div className="card !p-0 overflow-hidden">
          <div className="flex flex-col gap-3 border-b border-[var(--border)] p-4 lg:flex-row lg:items-center">
            <div className="relative flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--text-tertiary)]" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search name, phone, staff number or role"
                className="pl-9"
                aria-label="Search staff"
              />
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <div className="relative">
                <Select
                  value={organizationId}
                  onChange={(e) => setOrganizationId(e.target.value)}
                  className="h-12 appearance-none rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] pl-3 pr-9 text-sm font-medium"
                  aria-label="Organisation"
                >
                  {organizations.map((org) => (
                    <option key={org.id} value={org.id}>
                      {org.name}
                    </option>
                  ))}
                </Select>
                <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--text-tertiary)]" />
              </div>
              <div className="relative">
                <Select
                  value={branchFilter}
                  onChange={(e) => setBranchFilter(e.target.value)}
                  className="h-12 appearance-none rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] pl-3 pr-9 text-sm font-medium"
                  aria-label="Branch"
                >
                  <option value="">All branches</option>
                  {branches.map((branch) => (
                    <option key={branch.id} value={branch.id}>
                      {branch.name}
                    </option>
                  ))}
                </Select>
                <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--text-tertiary)]" />
              </div>
              <div className="relative">
                <Select
                  value={roleFilter}
                  onChange={(e) => setRoleFilter(e.target.value)}
                  className="h-12 appearance-none rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] pl-3 pr-9 text-sm font-medium"
                  aria-label="Role"
                >
                  <option value="">All roles</option>
                  {STAFF_ROLES.map((role) => (
                    <option key={role} value={role}>
                      {ROLE_LABELS[role]}
                    </option>
                  ))}
                </Select>
                <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--text-tertiary)]" />
              </div>
              <label className="flex h-12 cursor-pointer items-center gap-2 rounded-[var(--radius)] border border-[var(--border)] px-3 text-sm font-medium">
                <input
                  type="checkbox"
                  checked={includeInactive}
                  onChange={(e) => setIncludeInactive(e.target.checked)}
                  className="h-4 w-4 accent-[var(--primary)]"
                />
                Former staff
              </label>
            </div>
          </div>

          {loadError && (
            <div className="m-4 rounded-[var(--radius)] border border-[var(--danger-border)] bg-[var(--danger-bg)] px-4 py-3 text-sm text-[var(--danger)]">
              {loadError}
              <button
                className="ml-2 font-semibold underline"
                onClick={() => void loadStaffFor(organizationId)}
              >
                Retry
              </button>
            </div>
          )}

          {loading ? (
            <div className="space-y-3 p-4">
              {Array.from({ length: 5 }).map((_, index) => (
                <div key={index} className="flex items-center gap-3">
                  <Skeleton variant="circular" className="h-10 w-10" />
                  <div className="flex-1 space-y-2">
                    <Skeleton className="h-4 w-48" />
                    <Skeleton className="h-3 w-32" />
                  </div>
                  <Skeleton className="hidden h-8 w-28 sm:block" />
                </div>
              ))}
            </div>
          ) : filtered.length === 0 ? (
            <EmptyState
              icon={<Users className="h-6 w-6" />}
              title={staff.length === 0 ? "No staff yet" : "No matching staff"}
              description={
                staff.length === 0
                  ? "Add the first counter clerk or dispatcher so real work can leave your own account."
                  : "Adjust the search or filters to find the person you need."
              }
              action={
                canHireHere && staff.length === 0 ? (
                  <Button icon={<Plus className="h-4 w-4" />} onClick={openHire}>
                    Hire the first staff member
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <div className="overflow-x-auto">
              <table>
                <thead>
                  <tr>
                    <th>Person</th>
                    <th>Role</th>
                    <th>Workplace</th>
                    <th>Staff no.</th>
                    <th>Hired</th>
                    <th>Status</th>
                    <th className="text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((member) => (
                    <tr key={member.id} className={cn(!member.is_active && "opacity-60")}>
                      <td>
                        <div className="flex items-center gap-3">
                          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-[var(--surface-subtle)] text-xs font-bold text-[var(--text-secondary)] ring-1 ring-[var(--border)]">
                            {initials(member.full_name, member.phone)}
                          </div>
                          <div className="min-w-0">
                            <div className="font-semibold">
                              {member.full_name || "Unnamed person"}
                            </div>
                            <div className="flex items-center gap-1 text-xs text-[var(--text-tertiary)]">
                              <Phone className="h-3 w-3" />
                              {member.phone}
                              {member.email && (
                                <>
                                  <span className="px-1">·</span>
                                  <Mail className="h-3 w-3" />
                                  {member.email}
                                </>
                              )}
                            </div>
                          </div>
                        </div>
                      </td>
                      <td>
                        <Badge variant={roleBadge(member.role)}>{member.role_label}</Badge>
                        {member.job_title && (
                          <div className="mt-1 text-xs text-[var(--text-tertiary)]">
                            {member.job_title}
                          </div>
                        )}
                      </td>
                      <td>
                        {member.branch_name ? (
                          <span className="inline-flex items-center gap-1 text-sm">
                            <MapPin className="h-3.5 w-3.5 text-[var(--text-tertiary)]" />
                            {member.branch_name}
                          </span>
                        ) : (
                          <span className="text-sm text-[var(--text-tertiary)]">
                            Organisation-wide
                          </span>
                        )}
                      </td>
                      <td className="tabular-nums text-sm">
                        {member.employee_number || "—"}
                      </td>
                      <td className="text-sm text-[var(--text-secondary)]">
                        {formatDate(member.hired_at)}
                      </td>
                      <td>
                        {member.is_active ? (
                          <Badge variant="success">Active</Badge>
                        ) : (
                          <Badge variant="danger">Former</Badge>
                        )}
                      </td>
                      <td>
                        <div className="flex justify-end gap-1.5">
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={() => openEdit(member)}
                            disabled={!canManage}
                          >
                            Edit
                          </Button>
                          {member.is_active && (
                            <Button
                              size="sm"
                              variant="ghost"
                              icon={<UserX className="h-3.5 w-3.5" />}
                              onClick={() => setOffboarding(member)}
                              disabled={!canManage}
                              className="text-[var(--danger)] hover:bg-[var(--danger-bg)]"
                            >
                              Offboard
                            </Button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {managerBranch && (
          <p className="text-xs text-[var(--text-tertiary)]">
            As branch manager you can hire and manage counter staff at{" "}
            <span className="font-semibold text-[var(--text-secondary)]">
              {managerBranch.branch_name}
            </span>
            . Organisation-wide roles are managed by the operator administrator.
          </p>
        )}
      </div>

      <Modal
        open={hireOpen}
        onClose={() => setHireOpen(false)}
        title="Hire a staff member"
        footer={
          <>
            <Button variant="ghost" onClick={() => setHireOpen(false)}>
              Cancel
            </Button>
            <Button
              type="submit"
              form="hire-form"
              loading={hiring}
              icon={<UserPlus className="h-4 w-4" />}
            >
              Add staff member
            </Button>
          </>
        }
        className="max-w-2xl"
      >
        <form id="hire-form" onSubmit={submitHire}>
          <p className="mb-4 text-sm text-[var(--text-tertiary)]">
            Creating an account here is a hiring act: the person gets a sign-in and
            a role scoped to {selectedOrganization?.name ?? "this organisation"}.
          </p>
          {hireError && (
            <div className="mb-4 whitespace-pre-line rounded-[var(--radius)] border border-[var(--danger-border)] bg-[var(--danger-bg)] px-4 py-3 text-sm text-[var(--danger)]">
              {hireError}
            </div>
          )}
          <div className="grid gap-x-4 sm:grid-cols-2">
            <Field label="Full name" required>
              <Input
                value={hireForm.full_name}
                onChange={(e) => setHireForm({ ...hireForm, full_name: e.target.value })}
                placeholder="e.g. Wanjiku Kamau"
                autoFocus
              />
            </Field>
            <Field label="Phone number" required hint="Used as the sign-in identity.">
              <Input
                value={hireForm.phone}
                onChange={(e) => setHireForm({ ...hireForm, phone: e.target.value })}
                placeholder="07XX XXX XXX"
                inputMode="tel"
              />
            </Field>
            <Field label="Temporary password" hint="At least 8 characters. Share it with the new hire.">
              <Input
                type="password"
                value={hireForm.password}
                onChange={(e) => setHireForm({ ...hireForm, password: e.target.value })}
                placeholder="Only needed for a new phone"
                autoComplete="new-password"
              />
            </Field>
            <Field label="Email" hint="Optional.">
              <Input
                type="email"
                value={hireForm.email}
                onChange={(e) => setHireForm({ ...hireForm, email: e.target.value })}
                placeholder="name@example.com"
              />
            </Field>
            <Field label="Role" required>
              <Select
                value={hireForm.role}
                onChange={(e) => setHireForm({ ...hireForm, role: e.target.value as Role })}
                className="h-12 w-full rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-3 text-sm"
              >
                {assignableRoles.map((role) => (
                  <option key={role} value={role}>
                    {ROLE_LABELS[role]}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Branch" hint="Operating branch. Leave blank for HQ-wide roles.">
              <Select
                value={hireForm.branch}
                onChange={(e) => setHireForm({ ...hireForm, branch: e.target.value })}
                className="h-12 w-full rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-3 text-sm"
                disabled={Boolean(managerBranch)}
              >
                <option value="">Organisation-wide</option>
                {branches.map((branch) => (
                  <option key={branch.id} value={branch.id}>
                    {branch.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Job title" hint="Shown in the directory, e.g. Counter clerk.">
              <Input
                value={hireForm.job_title}
                onChange={(e) => setHireForm({ ...hireForm, job_title: e.target.value })}
                placeholder="Counter clerk"
              />
            </Field>
            <Field label="Staff number" hint="Operator-assigned, e.g. NKT-014.">
              <Input
                value={hireForm.employee_number}
                onChange={(e) =>
                  setHireForm({ ...hireForm, employee_number: e.target.value })
                }
                placeholder="NKT-014"
              />
            </Field>
            <Field label="Hire date">
              <Input
                type="date"
                value={hireForm.hired_at}
                onChange={(e) => setHireForm({ ...hireForm, hired_at: e.target.value })}
              />
            </Field>
          </div>
        </form>
      </Modal>

      <Modal
        open={Boolean(editing)}
        onClose={() => setEditing(null)}
        title="Edit staff record"
        footer={
          <>
            <Button variant="ghost" onClick={() => setEditing(null)}>
              Cancel
            </Button>
            <Button type="submit" form="edit-form" loading={savingEdit}>
              Save changes
            </Button>
          </>
        }
      >
        <form id="edit-form" onSubmit={submitEdit}>
          <div className="mb-4 flex items-center gap-3 rounded-[var(--radius)] bg-[var(--surface-subtle)] p-3">
            <div className="grid h-10 w-10 place-items-center rounded-full bg-[var(--surface)] text-sm font-bold ring-1 ring-[var(--border)]">
              {editing ? initials(editing.full_name, editing.phone) : ""}
            </div>
            <div>
              <div className="font-semibold">{editing?.full_name || editing?.phone}</div>
              <div className="text-xs text-[var(--text-tertiary)]">
                {editing?.phone} · hired {formatDate(editing?.hired_at ?? null)}
              </div>
            </div>
          </div>
          <div className="grid gap-x-4 sm:grid-cols-2">
            <Field label="Role">
              <Select
                value={editForm.role}
                onChange={(e) => setEditForm({ ...editForm, role: e.target.value as Role })}
                className="h-12 w-full rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-3 text-sm"
                disabled={Boolean(managerBranch) && !isOrgAdmin}
              >
                {assignableRoles.map((role) => (
                  <option key={role} value={role}>
                    {ROLE_LABELS[role]}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Branch">
              <Select
                value={editForm.branch}
                onChange={(e) => setEditForm({ ...editForm, branch: e.target.value })}
                className="h-12 w-full rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-3 text-sm"
              >
                <option value="">Organisation-wide</option>
                {branches.map((branch) => (
                  <option key={branch.id} value={branch.id}>
                    {branch.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Job title">
              <Input
                value={editForm.job_title}
                onChange={(e) => setEditForm({ ...editForm, job_title: e.target.value })}
              />
            </Field>
            <Field label="Staff number">
              <Input
                value={editForm.employee_number}
                onChange={(e) =>
                  setEditForm({ ...editForm, employee_number: e.target.value })
                }
              />
            </Field>
            <Field label="Hire date">
              <Input
                type="date"
                value={editForm.hired_at}
                onChange={(e) => setEditForm({ ...editForm, hired_at: e.target.value })}
              />
            </Field>
          </div>
        </form>
      </Modal>

      <Modal
        open={Boolean(offboarding)}
        onClose={() => setOffboarding(null)}
        title="Revoke access"
        footer={
          <>
            <Button variant="ghost" onClick={() => setOffboarding(null)}>
              Keep access
            </Button>
            <Button
              variant="danger"
              loading={offboardingBusy}
              onClick={() => void confirmOffboard()}
            >
              Revoke access
            </Button>
          </>
        }
      >
        <div className="flex gap-3">
          <div className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-[var(--danger-bg)] text-[var(--danger)]">
            <UserX className="h-5 w-5" />
          </div>
          <div>
            <p className="font-semibold">
              Offboard {offboarding?.full_name || offboarding?.phone}?
            </p>
            <p className="mt-1 text-sm text-[var(--text-tertiary)]">
              They will no longer be able to sign in, but trips, tickets and cash
              records they handled stay attached to their name for audit. You can
              reactivate them later from the former staff view.
            </p>
          </div>
        </div>
      </Modal>
    </div>
  );
}
