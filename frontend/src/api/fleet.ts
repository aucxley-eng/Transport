import { getJson, postJson } from "./client";

export interface Vehicle {
  id: string;
  organization: string;
  registration_number: string;
  vehicle_class: string;
  make?: string;
  model?: string;
  year?: number | null;
  seating_capacity: number;
  status: string;
  assigned_drivers?: string[];
}

export interface Driver {
  id: string;
  organization: string;
  user?: string | null;
  phone?: string;
  full_name?: string;
  license_number?: string;
  status: string;
  current_vehicle?: string | null;
}

export async function listVehicles(params?: Record<string, string>): Promise<Vehicle[]> {
  const search = params ? new URLSearchParams(params as any) : new URLSearchParams();
  const data = await getJson<any>(`/vehicles/?${search.toString()}`);
  return Array.isArray(data) ? data : data.results ?? [];
}

export async function listDrivers(params?: Record<string, string>): Promise<Driver[]> {
  const search = params ? new URLSearchParams(params as any) : new URLSearchParams();
  const data = await getJson<any>(`/drivers/?${search.toString()}`);
  return Array.isArray(data) ? data : data.results ?? [];
}

export async function approveDriver(id: string): Promise<Driver> {
  return postJson(`/drivers/${id}/approve/`, {});
}
