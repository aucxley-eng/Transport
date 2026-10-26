import { deleteJson, getJson, patchJson, postJson } from "./client";
import type {
  Branch,
  Membership,
  Organization,
  StaffHireInput,
  StaffMember,
} from "./types";

interface Paginated<T> {
  count: number;
  results: T[];
}

export interface StaffFilters {
  organization?: string;
  branch?: string;
  workplace?: string;
  role?: string;
  includeInactive?: boolean;
}

function query(params: Record<string, string | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) search.set(key, value);
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export async function listStaff(filters: StaffFilters = {}): Promise<StaffMember[]> {
  return getJson<StaffMember[]>(
    `/staff/${query({
      organization: filters.organization,
      branch: filters.branch,
      workplace: filters.workplace,
      role: filters.role,
      include_inactive: filters.includeInactive ? "1" : undefined,
    })}`
  );
}

export async function hireStaff(input: StaffHireInput): Promise<StaffMember> {
  return postJson<StaffMember>("/staff/", input);
}

export async function updateStaff(
  id: string,
  patch: Partial<
    Pick<
      Membership,
      "role" | "branch" | "workplace" | "job_title" | "employee_number" | "hired_at" | "ended_at" | "is_active"
    >
  >
): Promise<StaffMember> {
  return patchJson<StaffMember>(`/staff/${id}/`, patch);
}

export async function offboardStaff(id: string): Promise<void> {
  await deleteJson(`/staff/${id}/`);
}

export async function listMyOrganizations(): Promise<Organization[]> {
  const data = await getJson<Paginated<Organization> | Organization[]>("/organizations/");
  return Array.isArray(data) ? data : data.results;
}

export async function listBranches(organization: string): Promise<Branch[]> {
  const data = await getJson<Branch[]>(
    `/branches/${query({ organization, is_active: "1" })}`
  );
  return Array.isArray(data) ? data : [];
}
