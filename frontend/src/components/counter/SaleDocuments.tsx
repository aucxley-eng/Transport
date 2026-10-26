import { useState, type ReactNode } from "react";
import { Printer, ReceiptText, Ticket as TicketIcon } from "lucide-react";

import type { Ticket } from "@/api/booking";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";

type PrintTarget = "receipt" | "ticket" | "both";

interface SaleDocumentsProps {
  open: boolean;
  onClose: () => void;
  tickets: Ticket[];
  cashierName: string;
}

function money(minor: number, currency: string) {
  return `${currency} ${(minor / 100).toLocaleString("en-KE", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function formatDate(value: string) {
  const date = new Date(`${value}T00:00:00`);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString("en-KE", {
        day: "2-digit",
        month: "short",
        year: "numeric",
      });
}

function formatTime(value: string) {
  const [hours, minutes] = value.split(":");
  const date = new Date();
  date.setHours(Number(hours), Number(minutes), 0, 0);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleTimeString("en-KE", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
}

function CodeStrip({ value }: { value: string }) {
  let seed = 0;
  for (let index = 0; index < value.length; index += 1) {
    seed = (seed + value.charCodeAt(index) * (index + 3)) % 9973;
  }
  const bars = Array.from({ length: 32 }, () => {
    seed = (seed * 167 + 13) % 9973;
    return 1 + (seed % 3);
  });
  return (
    <div aria-hidden="true" className="flex h-9 items-stretch gap-[2px]">
      {bars.map((width, index) => (
        <span
          key={index}
          className="bg-[#111827]"
          style={{ width: `${width}px`, flex: `${width} 1 0` }}
        />
      ))}
    </div>
  );
}

function DocumentShell({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`print-doc mx-auto w-full max-w-[340px] rounded-xl border border-[color:var(--border)] bg-white p-4 text-[color:var(--text-primary)] shadow-[var(--shadow-sm)] ${className ?? ""}`}
    >
      {children}
    </div>
  );
}

export function SaleDocuments({
  open,
  onClose,
  tickets,
  cashierName,
}: SaleDocumentsProps) {
  const [printTarget, setPrintTarget] = useState<PrintTarget>("both");

  if (tickets.length === 0) return null;

  const first = tickets[0]!;
  const { operator, trip, payment, selling_branch, operating_branch } = first.document;
  const seats = tickets.map((ticket) => ticket.seat_number).sort((a, b) => a - b);
  const total = payment?.amount_minor ?? trip.fare_minor * tickets.length;
  const currency = payment?.currency ?? trip.currency;

  function print(target: PrintTarget) {
    setPrintTarget(target);
    window.setTimeout(() => window.print(), 120);
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Sale complete"
      className="max-w-4xl"
      footer={
        <div className="flex w-full flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-[color:var(--text-secondary)]">
            Hand the receipt and boarding ticket to the passenger.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button variant="ghost" size="sm" onClick={() => print("receipt")} className="gap-1.5">
              <ReceiptText className="h-4 w-4" />
              Receipt
            </Button>
            <Button variant="ghost" size="sm" onClick={() => print("ticket")} className="gap-1.5">
              <TicketIcon className="h-4 w-4" />
              Ticket
            </Button>
            <Button size="sm" onClick={() => print("both")} className="gap-1.5">
              <Printer className="h-4 w-4" />
              Print both
            </Button>
          </div>
        </div>
      }
    >
      <div
        id="print-root"
        className={
          printTarget === "receipt"
            ? "print-receipt"
            : printTarget === "ticket"
              ? "print-ticket"
              : "print-both"
        }
      >
        <div className="grid gap-4 md:grid-cols-2">
          <div className="doc-receipt">
            <DocumentShell>
              <div className="text-center">
                <p className="text-sm font-semibold uppercase tracking-[0.16em]">
                  {operator.name}
                </p>
                {operator.address ? (
                  <p className="mt-0.5 text-[11px] text-[color:var(--text-tertiary)]">
                    {operator.address}
                  </p>
                ) : null}
                <p className="text-[11px] text-[color:var(--text-tertiary)]">
                  {operator.contact_phone ? `${operator.contact_phone} · ` : ""}
                  {operator.kra_pin ? `PIN ${operator.kra_pin}` : "Payment receipt"}
                </p>
              </div>

              <div className="mt-3 border-y border-dashed border-[color:var(--border)] py-2 text-center">
                <p className="text-[10px] uppercase tracking-[0.22em] text-[color:var(--text-tertiary)]">
                  Payment receipt
                </p>
                <p className="mt-0.5 font-mono text-sm font-semibold">
                  {payment?.reference ??
                    first.document.order_id?.slice(0, 8).toUpperCase() ??
                    first.reference}
                </p>
              </div>

              <dl className="mt-3 space-y-1 text-[11px]">
                <Row label="Date" value={formatDate(trip.departure_date)} />
                <Row label="Cashier" value={cashierName} />
                <Row label="Passenger" value={first.passenger_name} />
                <Row label="Phone" value={first.passenger_phone} />
                <Row label="Route" value={`${trip.from_place} → ${trip.to_place}`} />
                <Row label="Seats" value={seats.join(", ")} />
                <Row label="Sold at" value={selling_branch?.name ?? operator.name} />
                {operating_branch && operating_branch.id !== selling_branch?.id ? (
                  <Row label="Operated by" value={operating_branch.name} />
                ) : null}
                <Row
                  label="Method"
                  value={
                    payment
                      ? payment.provider === "cash"
                        ? "Cash at counter"
                        : payment.provider.toUpperCase()
                      : "Pending"
                  }
                />
                <Row
                  label="Order"
                  value={
                    first.document.order_id
                      ? first.document.order_id.slice(0, 8).toUpperCase()
                      : "—"
                  }
                />
              </dl>

              <div className="mt-3 flex items-end justify-between border-t border-[color:var(--border)] pt-2">
                <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--text-tertiary)]">
                  Total paid
                </span>
                <span className="text-lg font-semibold">{money(total, currency)}</span>
              </div>

              <p className="mt-3 text-center text-[10px] leading-relaxed text-[color:var(--text-tertiary)]">
                Fare is for the journey shown. Keep this receipt for claims and
                refunds. Tax invoice is issued separately where applicable.
              </p>
            </DocumentShell>
          </div>

          <div className="doc-ticket space-y-4">
            {tickets.map((ticket) => (
              <DocumentShell key={ticket.id} className="doc-ticket-item">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="text-[10px] uppercase tracking-[0.2em] text-[color:var(--text-tertiary)]">
                      Boarding ticket
                    </p>
                    <p className="text-sm font-semibold uppercase tracking-[0.1em]">
                      {operator.name}
                    </p>
                  </div>
                  <div className="rounded-md bg-[color:var(--primary)] px-2 py-1 text-center text-white">
                    <p className="text-[9px] uppercase tracking-[0.16em]">Seat</p>
                    <p className="text-lg font-bold leading-none">{ticket.seat_number}</p>
                  </div>
                </div>

                <div className="mt-3 rounded-lg bg-[color:var(--surface-subtle)] p-2">
                  <div className="flex items-baseline justify-between gap-2">
                    <p className="text-sm font-semibold">{trip.from_place}</p>
                    <span className="text-[color:var(--text-tertiary)]">→</span>
                    <p className="text-sm font-semibold">{trip.to_place}</p>
                  </div>
                  <p className="mt-1 text-[11px] text-[color:var(--text-secondary)]">
                    {trip.route} · {formatDate(trip.departure_date)} at{" "}
                    {formatTime(trip.departure_time)}
                  </p>
                </div>

                <div className="mt-3 grid grid-cols-2 gap-2 text-[11px]">
                  <Field label="Passenger" value={ticket.passenger_name} />
                  <Field label="Phone" value={ticket.passenger_phone} />
                  <Field
                    label="Vehicle"
                    value={
                      trip.vehicle_registration
                        ? `${trip.vehicle_registration} (${trip.vehicle_class || "bus"})`
                        : "To be assigned"
                    }
                  />
                  <Field
                    label="ID"
                    value={ticket.passenger_id_number || "Not provided"}
                  />
                  <Field
                    label="Sold at"
                    value={selling_branch?.name ?? operator.name}
                  />
                  <Field
                    label="Operated by"
                    value={operating_branch?.name ?? "Same branch"}
                  />
                </div>

                <div className="mt-3 border-t border-dashed border-[color:var(--border)] pt-2">
                  <CodeStrip value={ticket.token} />
                  <p className="mt-1 text-center font-mono text-[11px] tracking-[0.16em]">
                    {ticket.reference}
                  </p>
                  <p className="text-center text-[9px] uppercase tracking-[0.14em] text-[color:var(--text-tertiary)]">
                    Present at boarding · gate checks ID
                  </p>
                </div>

                <p className="mt-2 text-center text-[9px] leading-relaxed text-[color:var(--text-tertiary)]">
                  Board 30 minutes before departure. Seats are only valid on the
                  date and route shown. Passengers must carry the original ID
                  used at booking.
                </p>
              </DocumentShell>
            ))}
          </div>
        </div>
      </div>
    </Modal>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-[color:var(--text-tertiary)]">{label}</dt>
      <dd className="text-right font-medium">{value || "—"}</dd>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[9px] uppercase tracking-[0.14em] text-[color:var(--text-tertiary)]">
        {label}
      </p>
      <p className="truncate font-medium">{value || "—"}</p>
    </div>
  );
}
