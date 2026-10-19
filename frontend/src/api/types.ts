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
