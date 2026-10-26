import { api, getList, postJson } from "./client";

export interface RouteStop {
  id: string;
  route: string;
  place: string;
  place_name: string;
  position: number;
  notes?: string;
}

export interface Route {
  id: string;
  organization: string;
  branch?: string | null;
  branch_name?: string | null;
  name: string;
  description?: string;
  is_active: boolean;
  stops: RouteStop[];
}

export interface Departure {
  id: string;
  organization: string;
  branch?: string | null;
  branch_name?: string | null;
  route: string;
  route_name: string;
  vehicle?: string | null;
  vehicle_class?: string;
  departure_date: string;
  departure_time: string;
  capacity: number;
  fare_minor: number;
  currency: string;
  status: string;
}

export interface Hold {
  id: string;
  seat_number: string;
  expires_at: string;
}

export interface TicketBranch {
  id: string;
  name: string;
  code: string;
}

export interface TicketDocument {
  operator: {
    name: string;
    address: string;
    contact_phone: string;
    kra_pin: string;
  };
  selling_branch: TicketBranch | null;
  operating_branch: TicketBranch | null;
  order_id: string | null;
  issued_at: string;
  trip: {
    route: string;
    from_place: string;
    to_place: string;
    departure_date: string;
    departure_time: string;
    vehicle_registration: string;
    vehicle_class: string;
    vehicle_make: string;
    vehicle_model: string;
    fare_minor: number;
    currency: string;
  };
  payment: {
    provider: string;
    reference: string | null;
    amount_minor: number;
    currency: string;
    collected_at: string;
    note: string;
  } | null;
}

export interface Ticket {
  id: string;
  departure: string;
  selling_branch: string | null;
  reference: string;
  token: string;
  seat_number: number;
  passenger_name: string;
  passenger_phone: string;
  passenger_id_number: string;
  state: string;
  boarded_at: string | null;
  boarding_info: Record<string, unknown>;
  document: TicketDocument;
}

export async function listTickets(params?: { order?: string; departure?: string }) {
  return getList<Ticket>("/tickets/", { params });
}

export async function listDepartures(params?: Record<string, unknown>) {
  return getList<Departure>("/departures/", { params });
}

export async function listRoutes(params?: Record<string, unknown>) {
  return getList<Route>("/routes/", { params });
}

export async function createHold(body: {
  departure: string;
  from_place: string;
  to_place: string;
  seats: number;
}) {
  return postJson<{ holds: Hold[] }>("/booking/holds/", body);
}

export async function checkoutHold(body: {
  departure: string;
  hold_ids: string[];
  passenger_name: string;
  passenger_phone: string;
  passenger_id_number?: string;
  selling_branch?: string | null;
}) {
  return postJson<{ order_id: string; total_minor: number; currency: string }>(
    "/booking/checkout/",
    body
  );
}

export async function searchDepartures(body: {
  origin_name?: string;
  destination_name?: string;
  date?: string;
}) {
  const { data } = await api.post<{ results: unknown[] }>("/booking/search/", body);
  return data.results ?? [];
}
