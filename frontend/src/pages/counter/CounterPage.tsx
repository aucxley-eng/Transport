import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Bus, Package, RefreshCw, Ticket, Truck } from "lucide-react";

import { useAuth } from "@/context/AuthContext";
import { api } from "@/api/client";
import {
  checkoutHold,
  createHold,
  listDepartures,
  listRoutes,
  listTickets,
  type Departure,
  type Route,
  type Ticket as BookingTicket,
} from "@/api/booking";
import { quoteParcel, createShipment, type ParcelQuote } from "@/api/goods";
import { recordCash, requestMpesa } from "@/api/payments";
import { listPlaces, listServices, type Service } from "@/api/catalog";
import { listBranches, type Branch } from "@/api/setup";
import { BusSeatMap } from "@/components/counter/BusSeatMap";
import { SaleDocuments } from "@/components/counter/SaleDocuments";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/toast";
import { cn } from "@/lib/utils";

type Tab = "ticket" | "parcel";

const TICKET_ROLES = ["branch_staff", "branch_manager", "operator_admin", "platform_admin"];

export function CounterPage() {
  const { user } = useAuth();
  const { toast } = useToast();
  const [tab, setTab] = useState<Tab>("ticket");
  const [loading, setLoading] = useState(false);
  const [departures, setDepartures] = useState<Departure[]>([]);
  const [routes, setRoutes] = useState<Route[]>([]);
  const [places, setPlaces] = useState<any[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [sellingBranch, setSellingBranch] = useState("");
  const [services, setServices] = useState<Service[]>([]);
  const [selectedDep, setSelectedDep] = useState<Departure | null>(null);

  const [fromPlace, setFromPlace] = useState("");
  const [toPlace, setToPlace] = useState("");
  const [seatMap, setSeatMap] = useState<{ number: number; state: string }[]>([]);
  const [selectedSeats, setSelectedSeats] = useState<number[]>([]);
  const [pName, setPName] = useState("");
  const [pPhone, setPPhone] = useState("");
  const [pId, setPId] = useState("");
  const [issuing, setIssuing] = useState(false);
  const [paymentMethod, setPaymentMethod] = useState<"cash" | "mpesa">("cash");
  const [saleTickets, setSaleTickets] = useState<BookingTicket[]>([]);
  const [saleOpen, setSaleOpen] = useState(false);

  const [sOrigin, setSOrigin] = useState("");
  const [sDest, setSDest] = useState("");
  const [sSender, setSSender] = useState("");
  const [sSenderPhone, setSSenderPhone] = useState("");
  const [sRecip, setSRecip] = useState("");
  const [sRecipPhone, setSRecipPhone] = useState("");
  const [sDesc, setSDesc] = useState("");
  const [sWeight, setSWeight] = useState("");
  const [quote, setQuote] = useState<ParcelQuote | null>(null);
  const [quoting, setQuoting] = useState(false);
  const [bookingParcel, setBookingParcel] = useState(false);

  const canUse = useMemo(
    () =>
      (user?.memberships ?? []).some((m) => m.is_active && TICKET_ROLES.includes(m.role)),
    [user]
  );

  const selectedRoute = useMemo(
    () => routes.find((route) => route.id === selectedDep?.route) ?? null,
    [routes, selectedDep]
  );
  const stops = selectedRoute?.stops ?? [];
  const branchPlaces = useMemo(
    () => places.filter((place) => ["branch", "hub", "warehouse"].includes(place.place_type)),
    [places]
  );
  const sellingBranches = useMemo(() => {
    const orgId = selectedDep?.organization;
    return branches.filter(
      (branch) => branch.is_active && (!orgId || branch.organization === orgId)
    );
  }, [branches, selectedDep]);
  const operatingBranch = useMemo(
    () =>
      branches.find((branch) => branch.id === selectedDep?.branch) ??
      (selectedDep?.branch_name ? { name: selectedDep.branch_name, id: selectedDep.branch ?? "" } : null),
    [branches, selectedDep]
  );
  const parcelService = useMemo(
    () =>
      services.find((s) => s.service_type === "parcel") ??
      services.find((s) => s.service_type === "courier") ??
      null,
    [services]
  );

  const load = async () => {
    setLoading(true);
    try {
      const organization = user?.memberships.find((m) => m.is_active)?.organization;
      const [deps, rts, pls, svcs, brs] = await Promise.all([
        listDepartures({ ordering: "departure_date" }),
        listRoutes(),
        organization ? listPlaces(organization).catch(() => []) : Promise.resolve([]),
        listServices().catch(() => []),
        listBranches().catch(() => []),
      ]);
      setDepartures(deps.results);
      setRoutes(rts.results);
      setPlaces(pls);
      setServices(svcs);
      setBranches(brs);
      const membershipBranch = user?.memberships.find(
        (member) => member.is_active && member.branch
      )?.branch;
      setSellingBranch((current) => current || membershipBranch || brs[0]?.id || "");
      const preferred = pls.find((place) => ["branch", "hub"].includes(place.place_type));
      if (preferred) {
        setSOrigin((current) => current || preferred.id);
      }
    } catch (e: any) {
      toast({
        title: "Could not load counter data",
        description: e?.message ?? "Try again.",
        type: "error",
      });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    if (stops.length >= 2) {
      setFromPlace(stops[0]?.place ?? "");
      setToPlace(stops[stops.length - 1]?.place ?? "");
    }
  }, [selectedDep, routes]);

  useEffect(() => {
    const loadSeats = async () => {
      if (!selectedDep || !fromPlace || !toPlace) {
        setSeatMap([]);
        setSelectedSeats([]);
        return;
      }
      try {
        const { data } = await api.get("/booking/seats/", { params: { departure: selectedDep.id, from_place: fromPlace, to_place: toPlace } });
        setSeatMap(data.seats || []);
      } catch {
        setSeatMap([]);
      }
    };
    void loadSeats();
  }, [selectedDep, fromPlace, toPlace]);

  async function sellTicket(e: FormEvent) {
    e.preventDefault();
    if (!selectedDep) {
      toast({ title: "Select a departure", description: "Choose a scheduled departure.", type: "error" });
      return;
    }
    if (!fromPlace || !toPlace) {
      toast({ title: "Select origin and destination", description: "Pick the boarding and alighting stops.", type: "error" });
      return;
    }
    if (!pName || !pPhone) {
      toast({ title: "Passenger details required", description: "Enter the passenger name and phone number.", type: "error" });
      return;
    }
    setIssuing(true);
    try {
      const seatCount = selectedSeats.length || 1;
      const held = await createHold({
        departure: selectedDep.id,
        from_place: fromPlace,
        to_place: toPlace,
        seats: seatCount,
      });
      const order = await checkoutHold({
        departure: selectedDep.id,
        hold_ids: held.holds.map((h) => h.id),
        passenger_name: pName,
        passenger_phone: pPhone,
        passenger_id_number: pId || undefined,
        selling_branch: sellingBranch || null,
      });
      if (paymentMethod === "cash") {
        await recordCash({
          order: order.order_id,
          amount_minor: order.total_minor,
          currency: order.currency,
          idempotency_key: crypto.randomUUID(),
          note: "Walk-in counter cash",
        });
        const { results } = await listTickets({ order: order.order_id });
        if (results.length > 0) {
          setSaleTickets(results);
          setSaleOpen(true);
        } else {
          toast({
            title: "Cash recorded but tickets not found",
            description: "Open bookings to reprint the documents.",
            type: "warning",
          });
        }
        toast({
          title: "Cash recorded and ticket issued",
          description: `Order ${order.order_id.slice(0, 8).toUpperCase()} · ${order.currency} ${(order.total_minor / 100).toFixed(2)}.`,
          type: "success",
        });
      } else {
        await requestMpesa({
          order: order.order_id,
          amount_minor: order.total_minor,
          currency: order.currency,
          phone: pPhone,
        });
        toast({
          title: "M-Pesa request sent",
          description: `Order ${order.order_id.slice(0, 8).toUpperCase()} is held. The ticket issues after the customer approves the prompt.`,
          type: "success",
        });
      }
      setPName("");
      setPPhone("");
      setPId("");
      setSelectedSeats([]);
      await load();
    } catch (e: any) {
      toast({
        title: "Ticket issuance failed",
        description: e?.response?.data?.detail || e?.message,
        type: "error",
      });
    } finally {
      setIssuing(false);
    }
  }

  async function getParcelQuote(e: FormEvent) {
    e.preventDefault();
    if (!parcelService) {
      toast({ title: "No parcel service", description: "Ask an administrator to configure an intercity parcel service.", type: "error" });
      return;
    }
    if (!sOrigin || !sDest) {
      toast({ title: "Choose collection and destination", description: "Both places are required.", type: "error" });
      return;
    }
    if (sOrigin === sDest) {
      toast({ title: "Choose different places", description: "Collection and destination cannot be the same.", type: "error" });
      return;
    }
    if (!sSender || !sSenderPhone || !sRecip || !sRecipPhone) {
      toast({ title: "Complete sender and recipient details", description: "Name and phone are required for both parties.", type: "error" });
      return;
    }
    if (!sWeight || Number(sWeight) <= 0) {
      toast({ title: "Enter the parcel weight", description: "Weight in kilograms is required to price the parcel.", type: "error" });
      return;
    }
    setQuoting(true);
    try {
      const now = new Date();
      const later = new Date(now.getTime() + 24 * 60 * 60 * 1000);
      const priced = await quoteParcel({
        service: parcelService.id,
        collection: sOrigin,
        destination: sDest,
        sender_name: sSender,
        sender_phone: sSenderPhone,
        service_window_start: now.toISOString(),
        service_window_end: later.toISOString(),
        parcels: [
          {
            recipient_name: sRecip,
            recipient_phone: sRecipPhone,
            recipient_place: sDest,
            description: sDesc || "Parcel",
            weight_kg: Number(sWeight),
          },
        ],
      });
      setQuote(priced);
    } catch (e: any) {
      toast({
        title: "Could not price the parcel",
        description: e?.response?.data?.detail || e?.message,
        type: "error",
      });
    } finally {
      setQuoting(false);
    }
  }

  async function confirmParcel() {
    if (!quote) return;
    setBookingParcel(true);
    try {
      const shipment = await createShipment({ quote: quote.quote, sender: true });
      toast({
        title: "Parcel booked",
        description: `Waybill ${shipment.reference}. Collect payment before release.`,
        type: "success",
      });
      setQuote(null);
      setSDesc("");
      setSWeight("");
    } catch (e: any) {
      toast({
        title: "Parcel intake failed",
        description: e?.response?.data?.detail || e?.message,
        type: "error",
      });
    } finally {
      setBookingParcel(false);
    }
  }

  function clearQuote() {
    if (quote) setQuote(null);
  }

  if (!canUse) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-10 sm:px-6 lg:px-8">
        <Card>
          <CardHeader>
            <CardTitle>Counter access restricted</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-[color:var(--text-subtle)]">
            Counter sales require branch staff, branch manager, or operator administrator access.
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-xs uppercase tracking-[0.18em] text-[color:var(--text-subtle)]">Walk-in</p>
          <h1 className="text-2xl font-semibold tracking-tight text-[color:var(--text)]">Counter workspace</h1>
          <p className="mt-1 text-sm text-[color:var(--text-subtle)]">Sell tickets and intake parcels for walk-in customers.</p>
        </div>
        <Button variant="ghost" size="sm" onClick={() => void load()} className="gap-2">
          <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          Refresh
        </Button>
      </header>

      <div className="flex gap-2 rounded-full border border-[color:var(--surface-border)] bg-white p-1 text-sm shadow-[var(--shadow-soft)]">
        <button
          onClick={() => setTab("ticket")}
          className={cn(
            "flex items-center gap-2 rounded-full px-4 py-2 transition",
            tab === "ticket" ? "bg-[color:var(--primary)] text-white" : "text-[color:var(--text-subtle)] hover:text-[color:var(--text)]"
          )}
        >
          <Ticket className="h-4 w-4" />
          Sell ticket
        </button>
        <button
          onClick={() => setTab("parcel")}
          className={cn(
            "flex items-center gap-2 rounded-full px-4 py-2 transition",
            tab === "parcel" ? "bg-[color:var(--primary)] text-white" : "text-[color:var(--text-subtle)] hover:text-[color:var(--text)]"
          )}
        >
          <Package className="h-4 w-4" />
          Send parcel
        </button>
      </div>

      {tab === "ticket" ? (
        <div className="grid gap-6 lg:grid-cols-[1.2fr_1fr]">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle className="flex items-center gap-2">
                <Bus className="h-4 w-4" />
                Scheduled departures
              </CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-2">
              {loading ? (
                <p className="text-sm text-[color:var(--text-subtle)]">Loading departures...</p>
              ) : departures.length === 0 ? (
                <p className="text-sm text-[color:var(--text-subtle)]">No departures are scheduled.</p>
              ) : (
                departures.map((d) => (
                  <button
                    key={d.id}
                    onClick={() => {
                      setSelectedDep(d);
                      const current = branches.find((branch) => branch.id === sellingBranch);
                      if (!current || (d.organization && current.organization !== d.organization)) {
                        const membershipBranch = user?.memberships.find(
                          (member) =>
                            member.is_active &&
                            member.branch &&
                            member.organization === d.organization
                        )?.branch;
                        const fallback =
                          branches.find(
                            (branch) =>
                              branch.is_active &&
                              branch.organization === d.organization &&
                              branch.is_head_office
                          )?.id ??
                          branches.find(
                            (branch) =>
                              branch.is_active && branch.organization === d.organization
                          )?.id;
                        setSellingBranch(membershipBranch ?? d.branch ?? fallback ?? "");
                      }
                    }}
                    className={cn(
                      "flex flex-col gap-1 rounded-xl border px-4 py-3 text-left transition",
                      selectedDep?.id === d.id
                        ? "border-[color:var(--primary)] bg-[color:rgba(0,107,96,0.06)]"
                        : "border-[color:var(--surface-border)] hover:border-[color:var(--primary)]/60"
                    )}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <p className="text-sm font-medium text-[color:var(--text)]">{d.route_name}</p>
                      <span className="text-xs uppercase tracking-[0.14em] text-[color:var(--text-subtle)]">{d.status}</span>
                    </div>
                    <p className="text-xs text-[color:var(--text-subtle)]">
                      {new Date(`${d.departure_date}T${d.departure_time}`).toLocaleString()} · {d.capacity} seats · {d.currency} {(d.fare_minor / 100).toFixed(2)} per seat
                    </p>
                    <p className="text-[11px] text-[color:var(--text-tertiary)]">
                      {d.branch_name ? `Operated by ${d.branch_name}` : "Operating branch not assigned"}
                      {d.branch && sellingBranch && d.branch !== sellingBranch
                        ? " · Cross-branch sale"
                        : ""}
                    </p>
                  </button>
                ))
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Issue ticket</CardTitle>
            </CardHeader>
            <CardContent>
              <form onSubmit={sellTicket} className="flex flex-col gap-4">
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label>Boarding at</Label>
                    <Select value={fromPlace} onChange={(e) => setFromPlace(e.target.value)} disabled={!selectedDep}>
                      <option value="">Select stop</option>
                      {stops.map((stop) => (
                        <option key={stop.id} value={stop.place}>{stop.place_name}</option>
                      ))}
                    </Select>
                  </div>
                  <div className="space-y-2">
                    <Label>Alighting at</Label>
                    <Select value={toPlace} onChange={(e) => setToPlace(e.target.value)} disabled={!selectedDep}>
                      <option value="">Select stop</option>
                      {stops.map((stop) => (
                        <option key={stop.id} value={stop.place}>{stop.place_name}</option>
                      ))}
                    </Select>
                  </div>
                </div>
                <div className="space-y-2">
                  <Label>Seat selection</Label>
                  {selectedDep && fromPlace && toPlace ? (
                    seatMap.length === 0 ? (
                      <p className="text-xs text-[color:var(--text-subtle)]">Loading seat map...</p>
                    ) : (
                      <div className="space-y-2">
                        <BusSeatMap
                          seats={seatMap}
                          selected={selectedSeats}
                          vehicleClass={selectedDep.vehicle_class}
                          onToggle={(seatNumber) =>
                            setSelectedSeats((prev) =>
                              prev.includes(seatNumber)
                                ? prev.filter((n) => n !== seatNumber)
                                : [...prev, seatNumber]
                            )
                          }
                        />
                        <p className="text-xs text-[color:var(--text-subtle)]">
                          {selectedSeats.length
                            ? `${selectedSeats.length} seat(s) selected: ${[...selectedSeats].sort((a, b) => a - b).join(", ")}`
                            : "No seat picked yet. Leave empty and the counter will assign the next free seat."}
                        </p>
                      </div>
                    )
                  ) : (
                    <p className="text-xs text-[color:var(--text-subtle)]">Select departure and boarding/alighting to see the seat map.</p>
                  )}
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label>Passenger name</Label>
                    <Input value={pName} onChange={(e) => setPName(e.target.value)} required />
                  </div>
                  <div className="space-y-2">
                    <Label>Phone</Label>
                    <Input value={pPhone} onChange={(e) => setPPhone(e.target.value)} placeholder="07xx xxx xxx" required />
                  </div>
                </div>
                <div className="space-y-2">
                  <Label>ID number (optional)</Label>
                  <Input value={pId} onChange={(e) => setPId(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label>Selling from</Label>
                  <Select
                    value={sellingBranch}
                    onChange={(event) => setSellingBranch(event.target.value)}
                    disabled={sellingBranches.length === 0}
                  >
                    <option value="">Select branch</option>
                    {sellingBranches.map((branch) => (
                      <option key={branch.id} value={branch.id}>
                        {branch.name}
                        {branch.is_head_office ? " · Head office" : ""}
                      </option>
                    ))}
                  </Select>
                  <p className="text-xs text-[color:var(--text-subtle)]">
                    Cash and the sale are booked here
                    {operatingBranch && operatingBranch.id !== sellingBranch
                      ? `; ${operatingBranch.name} operates the departure.`
                      : "."}
                  </p>
                </div>
                <div className="space-y-2">
                  <Label>Payment</Label>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => setPaymentMethod("cash")}
                      className={cn(
                        "flex-1 rounded-lg border px-3 py-2 text-sm transition",
                        paymentMethod === "cash"
                          ? "border-[color:var(--primary)] bg-[color:rgba(0,107,96,0.06)] text-[color:var(--text)]"
                          : "border-[color:var(--surface-border)] text-[color:var(--text-subtle)]"
                      )}
                    >
                      Cash at counter
                    </button>
                    <button
                      type="button"
                      onClick={() => setPaymentMethod("mpesa")}
                      className={cn(
                        "flex-1 rounded-lg border px-3 py-2 text-sm transition",
                        paymentMethod === "mpesa"
                          ? "border-[color:var(--primary)] bg-[color:rgba(0,107,96,0.06)] text-[color:var(--text)]"
                          : "border-[color:var(--surface-border)] text-[color:var(--text-subtle)]"
                      )}
                    >
                      M-Pesa prompt
                    </button>
                  </div>
                </div>
                <Button type="submit" disabled={issuing || !selectedDep} className="gap-2">
                  <Ticket className="h-4 w-4" />
                  {issuing
                    ? paymentMethod === "cash"
                      ? "Recording cash..."
                      : "Requesting payment..."
                    : paymentMethod === "cash"
                      ? "Take cash and issue ticket"
                      : "Send M-Pesa prompt"}
                </Button>
                <p className="text-xs text-[color:var(--text-subtle)]">
                  {paymentMethod === "cash"
                    ? "Cash is recorded against the order and the ticket is issued in one step."
                    : "The ticket is issued when the customer approves the M-Pesa prompt."}
                </p>
              </form>
            </CardContent>
          </Card>
        </div>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Truck className="h-4 w-4" />
              Parcel intake
            </CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={getParcelQuote} className="grid gap-4 lg:grid-cols-2">
              <div className="space-y-2">
                <Label>Collection branch</Label>
                <Select value={sOrigin} onChange={(e) => { setSOrigin(e.target.value); clearQuote(); }} required>
                  <option value="">Select branch</option>
                  {branchPlaces.map((place) => (
                    <option key={place.id} value={place.id}>{place.name}</option>
                  ))}
                </Select>
              </div>
              <div className="space-y-2">
                <Label>Destination</Label>
                <Select value={sDest} onChange={(e) => { setSDest(e.target.value); clearQuote(); }} required>
                  <option value="">Select destination</option>
                  {places.map((place) => (
                    <option key={place.id} value={place.id}>{place.name}</option>
                  ))}
                </Select>
              </div>
              <div className="space-y-2">
                <Label>Sender name</Label>
                <Input value={sSender} onChange={(e) => { setSSender(e.target.value); clearQuote(); }} required />
              </div>
              <div className="space-y-2">
                <Label>Sender phone</Label>
                <Input value={sSenderPhone} onChange={(e) => { setSSenderPhone(e.target.value); clearQuote(); }} required />
              </div>
              <div className="space-y-2">
                <Label>Recipient name</Label>
                <Input value={sRecip} onChange={(e) => { setSRecip(e.target.value); clearQuote(); }} required />
              </div>
              <div className="space-y-2">
                <Label>Recipient phone</Label>
                <Input value={sRecipPhone} onChange={(e) => { setSRecipPhone(e.target.value); clearQuote(); }} required />
              </div>
              <div className="space-y-2">
                <Label>Description</Label>
                <Input value={sDesc} onChange={(e) => setSDesc(e.target.value)} placeholder="Parcel contents" />
              </div>
              <div className="space-y-2">
                <Label>Weight (kg)</Label>
                <Input value={sWeight} onChange={(e) => { setSWeight(e.target.value); clearQuote(); }} placeholder="e.g. 2.5" required />
              </div>
              <div className="lg:col-span-2">
                <Button type="submit" disabled={quoting || !parcelService} className="gap-2">
                  <Package className="h-4 w-4" />
                  {quoting ? "Pricing..." : "Get quote"}
                </Button>
                {!parcelService ? (
                  <p className="mt-2 text-xs text-[color:var(--danger)]">
                    No intercity parcel service is configured for this organization.
                  </p>
                ) : null}
              </div>
            </form>

            {quote ? (
              <div className="mt-6 rounded-xl border border-[color:var(--primary)]/40 bg-[color:rgba(0,107,96,0.05)] p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <p className="text-xs uppercase tracking-[0.14em] text-[color:var(--text-subtle)]">Quoted total</p>
                    <p className="text-2xl font-semibold text-[color:var(--text)]">
                      {quote.currency} {(quote.total_minor / 100).toFixed(2)}
                    </p>
                    <p className="text-xs text-[color:var(--text-subtle)]">
                      {quote.rate_card || "Rate card"} · {quote.basis}
                    </p>
                  </div>
                  <Button onClick={() => void confirmParcel()} disabled={bookingParcel} className="gap-2">
                    <Package className="h-4 w-4" />
                    {bookingParcel ? "Booking..." : "Confirm and create waybill"}
                  </Button>
                </div>
              </div>
            ) : null}
          </CardContent>
        </Card>
      )}

      <SaleDocuments
        open={saleOpen}
        onClose={() => setSaleOpen(false)}
        tickets={saleTickets}
        cashierName={user?.full_name || "Counter staff"}
      />
    </div>
  );
}
