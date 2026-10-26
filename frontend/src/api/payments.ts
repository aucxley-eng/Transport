import { postJson } from "./client";

export interface PaymentAttempt {
  id: string;
  order: string;
  provider: string;
  state: string;
  expected_amount_minor: number;
  currency: string;
  provider_reference?: string | null;
  created_at?: string;
}

export async function recordCash(input: {
  order: string;
  amount_minor: number;
  currency: string;
  idempotency_key?: string;
  note?: string;
}): Promise<PaymentAttempt & { created?: boolean; order_status?: string }> {
  return postJson("/payments/cash/", input);
}

export async function requestMpesa(input: {
  order: string;
  amount_minor: number;
  currency: string;
  phone: string;
  provider?: string;
}): Promise<PaymentAttempt> {
  return postJson("/payments/", { ...input, provider: input.provider ?? "mpesa" });
}
