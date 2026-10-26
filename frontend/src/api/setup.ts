import { getJson, patchJson, postJson } from "./client";

export interface Organization {
  id: string;
  name: string;
  slug: string;
  currency?: string;
  org_type?: string;
  approval_status?: string;
  timezone?: string;
  address?: string;
  contact_phone?: string;
  is_active?: boolean;
}

export interface Place {
  id: string;
  organization: string | null;
  name: string;
  place_type: string;
  lat?: number | null;
  lng?: number | null;
  address?: string;
  is_active: boolean;
}

export interface Branch {
  id: string;
  organization: string;
  organization_name?: string;
  name: string;
  code: string;
  address: string;
  contact_phone: string;
  is_head_office: boolean;
  is_active: boolean;
}

export interface Vehicle {
  id: string;
  organization: string;
  branch?: string | null;
  branch_name?: string | null;
  registration_number: string;
  vehicle_class: string;
  make?: string;
  model?: string;
  year?: number | null;
  seating_capacity: number;
  status: string;
}

export interface Route {
  id: string;
  organization: string;
  branch?: string | null;
  branch_name?: string | null;
  name: string;
  description?: string;
  is_active: boolean;
}

export interface RouteStop {
  id: string;
  route: string;
  place: string;
  place_name?: string;
  position: number;
  notes?: string;
}

export interface Departure {
  id: string;
  organization: string;
  branch?: string | null;
  branch_name?: string | null;
  route: string;
  route_name?: string;
  vehicle?: string | null;
  vehicle_class?: string;
  departure_date: string;
  departure_time: string;
  capacity: number;
  fare_minor: number;
  currency: string;
  status: string;
}

export interface Service {
  id: string;
  slug: string;
  name: string;
  service_type: string;
  is_active: boolean;
}

export async function listOrganizations(): Promise<Organization[]> {
  const data = await getJson<any>("/organizations/");
  return Array.isArray(data) ? data : data.results ?? [];
}

export async function createOrganization(body: Partial<Organization>): Promise<Organization> {
  return postJson("/organizations/", body);
}

export async function getOrganization(id: string): Promise<Organization> {
  return getJson<Organization>(`/organizations/${id}/`);
}

export async function approveOrganization(id: string): Promise<Organization> {
  return postJson<Organization>(`/organizations/${id}/approve/`, {});
}

export async function suspendOrganization(id: string): Promise<Organization> {
  return postJson<Organization>(`/organizations/${id}/suspend/`, {});
}

export async function listBranches(params?: Record<string, string>): Promise<Branch[]> {
  const search = params ? new URLSearchParams(params) : new URLSearchParams();
  const data = await getJson<any>(`/branches/?${search.toString()}`);
  return Array.isArray(data) ? data : data.results ?? [];
}

export async function getBranch(id: string): Promise<Branch> {
  return getJson<Branch>(`/branches/${id}/`);
}

export async function createBranch(body: Partial<Branch>): Promise<Branch> {
  return postJson<Branch>("/branches/", body);
}

export async function updateBranch(
  id: string,
  body: Partial<Branch>
): Promise<Branch> {
  return patchJson<Branch>(`/branches/${id}/`, body);
}

export async function listPlaces(params?: Record<string, string>): Promise<Place[]> {
  const search = new URLSearchParams(params as any);
  const data = await getJson<any>(`/places/?${search.toString()}`);
  return Array.isArray(data) ? data : data.results ?? [];
}

export async function createPlace(body: Partial<Place>): Promise<Place> {
  return postJson("/places/", body);
}

export async function listVehicles(params?: Record<string, string>): Promise<Vehicle[]> {
  const search = params ? new URLSearchParams(params as any) : new URLSearchParams();
  const data = await getJson<any>(`/vehicles/?${search.toString()}`);
  return Array.isArray(data) ? data : data.results ?? [];
}

export async function createVehicle(body: Partial<Vehicle>): Promise<Vehicle> {
  return postJson("/vehicles/", body);
}

export async function listServices(): Promise<Service[]> {
  const data = await getJson<any>("/services/");
  return Array.isArray(data) ? data : data.results ?? [];
}

export async function listRoutes(params?: Record<string, string>): Promise<Route[]> {
  const search = params ? new URLSearchParams(params as any) : new URLSearchParams();
  const data = await getJson<any>(`/routes/?${search.toString()}`);
  return Array.isArray(data) ? data : data.results ?? [];
}

export async function createRoute(body: Partial<Route>): Promise<Route> {
  return postJson("/routes/", body);
}

export async function createRouteStop(body: Partial<RouteStop>): Promise<RouteStop> {
  return postJson("/route-stops/", body);
}

export async function listDepartures(params?: Record<string, string>): Promise<Departure[]> {
  const search = params ? new URLSearchParams(params as any) : new URLSearchParams();
  const data = await getJson<any>(`/departures/?${search.toString()}`);
  return Array.isArray(data) ? data : data.results ?? [];
}

export async function createDeparture(body: Partial<Departure>): Promise<Departure> {
  return postJson("/departures/", body);
}
