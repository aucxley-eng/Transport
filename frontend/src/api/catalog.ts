import { getJson } from "./client";
import type { Place } from "./types";

interface Paginated<T> {
  count: number;
  results: T[];
}

export interface Service {
  id: string;
  slug: string;
  name: string;
  service_type: string;
  is_active: boolean;
}

export async function listPlaces(organization?: string): Promise<Place[]> {
  const search = new URLSearchParams({ is_active: "true" });
  if (organization) search.set("organization", organization);
  const data = await getJson<Paginated<Place> | Place[]>(`/places/?${search.toString()}`);
  return Array.isArray(data) ? data : data.results;
}

export async function listServices(): Promise<Service[]> {
  const data = await getJson<Paginated<Service> | Service[]>("/services/");
  return Array.isArray(data) ? data : data.results;
}
