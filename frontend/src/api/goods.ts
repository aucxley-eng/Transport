import { getList, postJson } from "./client";

export interface Shipment {
  id: string;
  reference: string;
  organization: string;
  status: string;
  package_count?: number;
  sender_name?: string | null;
  sender_phone?: string | null;
  recipient_name?: string | null;
  recipient_phone?: string | null;
  agreed_total_minor?: number;
  currency?: string;
  created_at?: string;
}

export interface ParcelQuote {
  quote: string;
  total_minor: number;
  currency: string;
  basis: string;
  rate_card: string;
  parcels: unknown[];
  expires_at: string | null;
}

export async function listShipments(params?: Record<string, unknown>) {
  return getList<Shipment>("/shipments/", { params });
}

export async function quoteParcel(body: Record<string, unknown>): Promise<ParcelQuote> {
  return postJson<ParcelQuote>("/shipments/parcel-quote/", body);
}

export async function createShipment(body: Record<string, unknown>) {
  return postJson<Shipment>("/shipments/", body);
}
