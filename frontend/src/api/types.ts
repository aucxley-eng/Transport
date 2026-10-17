/**
 * Domain types mirrored from the backend.
 *
 * Hand-written rather than generated: the API surface is large and mostly
 * irrelevant to any one screen, and a generated client would be a second thing
 * to keep in step with DRF serialisers. These cover what the shell needs; each
 * feature adds its own alongside its pages.
 *
 * The string unions mirror the backend enums exactly -- `Role` in
 * apps/accounts/models.py:125, `Organization.Approval` and `.Type` further up
 * the same file. If they drift, TypeScript will not catch it, so a change there
 * is a change here.
 */

export type Role =
  | "customer"
  | "recipient"
  | "driver"
  | "conductor"
  | "branch_staff"
  | "dispatcher"
  | "operator_admin"
  | "corporate_manager"
  | "support_agent"
  | "finance_officer"
  | "platform_admin";

export type OrganizationApproval =
  | "pending"
  | "approved"
  | "rejected"
  | "suspended";

/** What `GET /api/v1/me/` returns, including the caller's memberships. */
export interface Membership {
  id: string;
  organization: string;
  organization_name: string;
  organization_slug: string;
  role: Role;
  is_active: boolean;
}

export interface CurrentUser {
  id: string;
  phone: string;
  email: string | null;
  full_name: string;
  memberships: Membership[];
}

/**
 * Roles that may manage an organization's records.
 *
 * Mirrors `Role.elevated()` in apps/accounts/models.py. The UI uses it to decide
 * what to show -- the API enforces it regardless, so a hidden control is a
 * convenience, never a security boundary.
 */
const ELEVATED_ROLES = new Set<Role>([
  "operator_admin",
  "dispatcher",
  "branch_staff",
  "finance_officer",
  "support_agent",
  "platform_admin",
]);

export function hasElevatedRole(user: CurrentUser | null): boolean {
  if (user === null) return false;
  return user.memberships.some(
    (m) => m.is_active && ELEVATED_ROLES.has(m.role),
  );
}

export function isDispatcher(user: CurrentUser | null): boolean {
  if (user === null) return false;
  return user.memberships.some(
    (m) => m.is_active && (m.role === "dispatcher" || m.role === "operator_admin"),
  );
}
