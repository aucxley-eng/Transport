import { BusFront } from "lucide-react";

import { cn } from "@/lib/utils";

export interface SeatMapSeat {
  number: number;
  state: string;
}

interface BusSeatMapProps {
  seats: SeatMapSeat[];
  selected: number[];
  onToggle: (seatNumber: number) => void;
  vehicleClass?: string;
  disabled?: boolean;
}

function layoutFor(vehicleClass?: string) {
  const value = (vehicleClass || "bus").toLowerCase();
  if (value === "sedan") {
    return { pattern: [0, -1, 1], seatsPerRow: 2 };
  }
  if (value === "van" || value === "shuttle") {
    return { pattern: [0, 1, -1, 2], seatsPerRow: 3 };
  }
  return { pattern: [0, 1, -1, 2, 3], seatsPerRow: 4 };
}

export function BusSeatMap({
  seats,
  selected,
  onToggle,
  vehicleClass,
  disabled = false,
}: BusSeatMapProps) {
  const { pattern, seatsPerRow } = layoutFor(vehicleClass);
  const rows: SeatMapSeat[][] = [];
  for (let index = 0; index < seats.length; index += seatsPerRow) {
    rows.push(seats.slice(index, index + seatsPerRow));
  }

  const taken = seats.filter((seat) => seat.state === "taken").length;
  const free = seats.length - taken;

  return (
    <div className="rounded-2xl border border-[color:var(--border)] bg-[linear-gradient(180deg,var(--surface)_0%,var(--surface-subtle)_100%)] p-3">
      <div className="flex items-center justify-between px-1 text-[11px] font-medium uppercase tracking-[0.16em] text-[color:var(--text-tertiary)]">
        <span>Front</span>
        <span className="flex items-center gap-1.5 text-[color:var(--text-secondary)]">
          <BusFront className="h-4 w-4" aria-hidden="true" />
          {vehicleClass || "bus"}
        </span>
      </div>

      <div className="mt-1 flex items-center gap-3 px-1">
        <div className="h-8 w-9 rounded-md border-2 border-[color:var(--border-strong)] bg-[color:var(--surface)]" />
        <div className="h-1 flex-1 rounded-full bg-[color:var(--border)]" />
        <span className="text-[10px] uppercase tracking-[0.14em] text-[color:var(--text-tertiary)]">
          Driver
        </span>
      </div>

      <div
        role="group"
        aria-label="Vehicle seat map"
        className="mt-3 flex flex-col gap-2"
      >
        {rows.map((row, rowIndex) => (
          <div
            key={`row-${rowIndex}`}
            className="grid items-center gap-1.5"
            style={{
              gridTemplateColumns: pattern
                .map((slot) => (slot === -1 ? "14px" : "minmax(0, 1fr)"))
                .join(" "),
            }}
          >
            {pattern.map((slot, slotIndex) => {
              const seat = slot === -1 ? undefined : row[slot];
              if (slot === -1) {
                return (
                  <span
                    key={`aisle-${rowIndex}-${slotIndex}`}
                    aria-hidden="true"
                    className="text-center text-[10px] text-[color:var(--text-tertiary)]"
                  >
                    {rowIndex === Math.floor(rows.length / 2) ? "·" : ""}
                  </span>
                );
              }
              if (!seat) {
                return <span key={`empty-${rowIndex}-${slotIndex}`} />;
              }
              const isTaken = seat.state === "taken";
              const isSelected = selected.includes(seat.number);
              return (
                <button
                  type="button"
                  key={seat.number}
                  disabled={disabled || isTaken}
                  aria-pressed={isSelected}
                  aria-label={`Seat ${seat.number}, ${
                    isTaken ? "taken" : isSelected ? "selected" : "free"
                  }`}
                  onClick={() => onToggle(seat.number)}
                  className={cn(
                    "flex h-11 items-center justify-center rounded-lg border text-xs font-medium transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--primary)] focus-visible:ring-offset-1",
                    isTaken
                      ? "cursor-not-allowed border-[color:var(--border)] bg-[color:var(--surface-subtle)] text-[color:var(--text-tertiary)] line-through"
                      : isSelected
                        ? "border-[color:var(--primary)] bg-[color:var(--primary)] text-[color:var(--primary-foreground)] shadow-[var(--shadow-sm)]"
                        : "border-[color:var(--border)] bg-[color:var(--surface)] text-[color:var(--text-secondary)] hover:border-[color:var(--primary)] hover:text-[color:var(--text-primary)]"
                  )}
                >
                  {seat.number}
                </button>
              );
            })}
          </div>
        ))}
      </div>

      <div className="mt-3 flex items-center justify-between border-t border-dashed border-[color:var(--border)] pt-2 text-[11px] text-[color:var(--text-tertiary)]">
        <span>Rear</span>
        <span>
          {free} free · {taken} taken
        </span>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-[color:var(--text-tertiary)]">
        <span className="flex items-center gap-1.5">
          <span className="h-3 w-3 rounded border border-[color:var(--border)] bg-[color:var(--surface)]" />
          Free
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-3 w-3 rounded border border-[color:var(--primary)] bg-[color:var(--primary)]" />
          Selected
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-3 w-3 rounded border border-[color:var(--border)] bg-[color:var(--surface-subtle)]" />
          Taken
        </span>
      </div>
    </div>
  );
}
