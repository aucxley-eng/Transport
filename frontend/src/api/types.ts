/**
 * Domain types mirrored from the backend.
 *
 * Hand-written rather than generated: the API surface is large and mostly
 * irrelevant to any one screen, and a generated client would be a second thing
 * to keep in step with DRF serialisers. These cover what the shell needs; each
 * feature adds its own alongside its pages.
 *
 * The string unions mirror the backend enums exactly -- `Role` in
 * apps/accounts/models.py:125. If they drift, TypeScript will not catch it, so a
 * change there is a change here.
 */

export type Role =
  | "customer"
  | "recipient"
  | "driver"
  | "conductor"
  | "branch_staff"
  | "branch_manager"
  | "dispatcher"
  | "operator_admin"
  | "corporate_manager"
  | "support_agent"
  | "finance_officer"
  | "platform_admin";

export const STAFF_ROLES: Role[] = [
  "driver",
  "conductor",
  "branch_staff",
  "branch_manager",
  "dispatcher",
  "operator_admin",
  "corporate_manager",
  "support_agent",
  "finance_officer",
  "platform_admin",
];

export const ROLE_LABELS: Record<Role, string> = {
  customer: "Customer or sender",
  recipient: "Recipient",
  driver: "Driver",
  conductor: "Conductor",
  branch_staff: "Counter or warehouse staff",
  branch_manager: "Branch manager",
  dispatcher: "Dispatcher",
  operator_admin: "Operator administrator",
  corporate_manager: "Corporate travel manager",
  support_agent: "Support agent",
  finance_officer: "Finance officer",
  platform_admin: "Platform administrator",
};

/** What `GET /api/v1/me/` returns, including the caller's memberships. */
export interface Branch {
  id: string;
  organization: string;
  organization_name: string;
  name: string;
  code: string;
  address: string;
  contact_phone: string;
  is_head_office: boolean;
  is_active: boolean;
  created_at: string;
}

export interface Membership {
  id: string;
  organization: string;
  organization_name: string;
  organization_slug: string;
  role: Role;
  branch: string | null;
  branch_name: string | null;
  branch_code: string | null;
  workplace: string | null;
  workplace_name: string | null;
  job_title: string;
  employee_number: string;
  hired_at: string | null;
  ended_at: string | null;
  is_active: boolean;
}

export interface CurrentUser {
  id: string;
  phone: string;
  email: string | null;
  full_name: string;
  memberships: Membership[];
}

export interface Place {
  id: string;
  organization: string | null;
  name: string;
  place_type: "stage" | "branch" | "hub" | "warehouse" | "landmark";
  lat: string | null;
  lng: string | null;
  address: string;
  landmark: string;
  directions: string;
  is_active: boolean;
}

export interface StaffMember {
  id: string;
  user_id: string;
  phone: string;
  full_name: string;
  email: string | null;
  organization: string;
  organization_name: string;
  branch: string | null;
  branch_name: string | null;
  branch_code: string | null;
  workplace: string | null;
  workplace_name: string | null;
  role: Role;
  role_label: string;
  job_title: string;
  employee_number: string;
  hired_at: string | null;
  ended_at: string | null;
  is_active: boolean;
  created_at: string;
}

export interface StaffHireInput {
  organization: string;
  phone: string;
  full_name?: string;
  email?: string;
  password?: string;
  role: Role;
  branch?: string | null;
  workplace?: string | null;
  job_title?: string;
  employee_number?: string;
  hired_at?: string | null;
}

export interface Organization {
  id: string;
  name: string;
  slug: string;
  org_type: "carrier" | "platform" | "corporate";
  approval_status: "pending" | "approved" | "rejected" | "suspended";
  currency: string;
  timezone: string;
  address: string;
  contact_phone: string;
  is_active: boolean;
}
