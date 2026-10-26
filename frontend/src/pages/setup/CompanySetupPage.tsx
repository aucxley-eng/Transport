import { useEffect, useState, type FormEvent } from "react";
import { Building2, ChevronRight, MapPin, Plus, Save } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";

import { describeError } from "@/api/client";
import { useAuth } from "@/context/AuthContext";
import {
  createOrganization,
  createPlace,
  listOrganizations,
  listPlaces,
  type Organization,
  type Place,
} from "@/api/setup";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/toast";

const PLACE_TYPES = [
  { value: "hub", label: "Hub" },
  { value: "warehouse", label: "Warehouse" },
  { value: "stage", label: "Stage / stop" },
  { value: "landmark", label: "Landmark" },
];

export function CompanySetupPage() {
  const { user } = useAuth();
  const { toast } = useToast();
  const [searchParams] = useSearchParams();
  const requestedOrg = searchParams.get("org") ?? "";
  const [orgs, setOrgs] = useState<Organization[]>([]);
  const [places, setPlaces] = useState<Place[]>([]);
  const [, setLoading] = useState(false);

  const [orgName, setOrgName] = useState("");
  const [orgSlug, setOrgSlug] = useState("");
  const [creatingOrg, setCreatingOrg] = useState(false);

  const [placeOrg, setPlaceOrg] = useState(requestedOrg);
  const [placeName, setPlaceName] = useState("");
  const [placeType, setPlaceType] = useState("stage");
  const [placeAddress, setPlaceAddress] = useState("");
  const [creatingPlace, setCreatingPlace] = useState(false);

  const canSetup = (user?.memberships ?? []).some(
    (m) => m.is_active && ["operator_admin", "platform_admin"].includes(m.role)
  );

  // Only platform administrators may onboard a new organization; an operator
  // admin can still add places to the organizations they belong to.
  const canCreateOrg = (user?.memberships ?? []).some(
    (m) => m.is_active && m.role === "platform_admin"
  );

  const load = async () => {
    setLoading(true);
    try {
      const [o, p] = await Promise.all([listOrganizations(), listPlaces()]);
      setOrgs(o);
      setPlaces(p);
      if (requestedOrg) setPlaceOrg(requestedOrg);
      else if (!placeOrg && user?.memberships[0]?.organization) setPlaceOrg(user.memberships[0].organization);
      else if (!placeOrg && o[0]) setPlaceOrg(o[0].id);
    } catch (e: any) {
      toast({ title: "Could not load setup data", description: e?.message, type: "error" });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  async function onCreateOrg(e: FormEvent) {
    e.preventDefault();
    setCreatingOrg(true);
    try {
      const created = await createOrganization({ name: orgName, slug: orgSlug });
      toast({ title: "Organization created", description: created.name, type: "success" });
      setOrgName("");
      setOrgSlug("");
      // Show it immediately. The reload below can fail on a flaky connection;
      // that must not make a successful create look like it vanished.
      setOrgs((current) => [...current, created]);
      setPlaceOrg(created.id);
      await load();
    } catch (error) {
      toast({
        title: "Failed to create organization",
        description: describeError(error),
        type: "error",
      });
    } finally {
      setCreatingOrg(false);
    }
  }

  async function onCreatePlace(e: FormEvent) {
    e.preventDefault();
    setCreatingPlace(true);
    try {
      await createPlace({
        organization: placeOrg || undefined,
        name: placeName,
        place_type: placeType,
        address: placeAddress,
        is_active: true,
      });
      toast({ title: "Place added", description: placeName, type: "success" });
      setPlaceName("");
      setPlaceAddress("");
      await load();
    } catch (e: any) {
      toast({ title: "Failed to create place", description: e?.response?.data?.detail || e?.message, type: "error" });
    } finally {
      setCreatingPlace(false);
    }
  }

  if (!canSetup) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-10 sm:px-6 lg:px-8">
        <Card>
          <CardHeader>
            <CardTitle>Setup access restricted</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-[color:var(--text-subtle)]">
            Only operator or platform administrators can create organizations and places.
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
      <header>
        <p className="text-xs uppercase tracking-[0.18em] text-[color:var(--text-subtle)]">Setup</p>
        <h1 className="text-2xl font-semibold tracking-tight text-[color:var(--text)]">Company setup</h1>
        <p className="mt-1 text-sm text-[color:var(--text-subtle)]">
          Create your organisation and its map places. Operating branches,
          their teams and fleet live under Branches.
        </p>
      </header>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Building2 className="h-4 w-4" />
              Organization
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            {canCreateOrg ? (
              <form onSubmit={onCreateOrg} className="flex flex-col gap-3">
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label>Company name</Label>
                    <Input value={orgName} onChange={(e) => { setOrgName(e.target.value); setOrgSlug(e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "")); }} required />
                  </div>
                  <div className="space-y-2">
                    <Label>Slug</Label>
                    <Input value={orgSlug} onChange={(e) => setOrgSlug(e.target.value)} required />
                  </div>
                </div>
                <Button type="submit" disabled={creatingOrg} className="gap-2">
                  <Plus className="h-4 w-4" />
                  {creatingOrg ? "Creating..." : "Create organization"}
                </Button>
              </form>
            ) : (
              <p className="rounded-lg border border-[color:var(--surface-border)] bg-[color:var(--surface-muted)] px-3 py-2 text-sm text-[color:var(--text-subtle)]">
                Only platform administrators can onboard a new organization.
              </p>
            )}
            <div>
              <p className="mb-2 text-xs uppercase tracking-[0.14em] text-[color:var(--text-subtle)]">Existing</p>
              <div className="flex flex-col gap-2">
                {orgs.map((o) => (
                  <Link
                    key={o.id}
                    to={`/organisations/${o.id}`}
                    className="flex items-center justify-between gap-3 rounded-lg border border-[color:var(--surface-border)] px-3 py-2 text-sm transition-colors hover:border-[color:var(--primary)]/40 hover:bg-[color:var(--surface-muted)]"
                  >
                    <span className="flex flex-col">
                      <span className="font-medium text-[color:var(--text)]">{o.name}</span>
                      <span className="text-xs text-[color:var(--text-subtle)]">
                        {o.slug}
                        {o.approval_status ? ` · ${o.approval_status}` : ""}
                      </span>
                    </span>
                    <ChevronRight className="h-4 w-4 text-[color:var(--text-subtle)]" />
                  </Link>
                ))}
                {orgs.length === 0 ? <p className="text-sm text-[color:var(--text-subtle)]">None yet.</p> : null}
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <MapPin className="h-4 w-4" />
              Stops, stages & landmarks
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="mb-3 flex items-center justify-between gap-3 rounded-lg border border-[color:var(--surface-border)] bg-[color:var(--surface-muted)] px-3 py-2 text-sm">
              <span className="text-[color:var(--text-subtle)]">
                Looking for the operating branch list?
              </span>
              <Link
                to={placeOrg ? `/branches?org=${placeOrg}` : "/branches"}
                className="shrink-0 font-medium text-[color:var(--primary)]"
              >
                Manage branches
              </Link>
            </div>
            <form onSubmit={onCreatePlace} className="flex flex-col gap-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label>Organization</Label>
                  <Select value={placeOrg} onChange={(e) => setPlaceOrg(e.target.value)}>
                    <option value="">Platform / shared</option>
                    {orgs.map((o) => (
                      <option key={o.id} value={o.id}>{o.name}</option>
                    ))}
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Type</Label>
                  <Select value={placeType} onChange={(e) => setPlaceType(e.target.value)}>
                    {PLACE_TYPES.map((t) => (
                      <option key={t.value} value={t.value}>{t.label}</option>
                    ))}
                  </Select>
                </div>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label>Name</Label>
                  <Input value={placeName} onChange={(e) => setPlaceName(e.target.value)} placeholder="e.g. Nairobi CBD" required />
                </div>
                <div className="space-y-2">
                  <Label>Address (optional)</Label>
                  <Input value={placeAddress} onChange={(e) => setPlaceAddress(e.target.value)} />
                </div>
              </div>
              <Button type="submit" disabled={creatingPlace} className="gap-2">
                <Save className="h-4 w-4" />
                {creatingPlace ? "Saving..." : "Add place"}
              </Button>
            </form>
            <div className="mt-4">
              <p className="mb-2 text-xs uppercase tracking-[0.14em] text-[color:var(--text-subtle)]">Added ({places.length})</p>
              <div className="max-h-[220px] overflow-auto rounded-xl border border-[color:var(--surface-border)]">
                <table className="w-full text-sm">
                  <thead className="bg-[color:var(--surface-subtle)]">
                    <tr>
                      <th className="px-3 py-2 text-left font-medium">Name</th>
                      <th className="px-3 py-2 text-left font-medium">Type</th>
                      <th className="px-3 py-2 text-left font-medium">Address</th>
                    </tr>
                  </thead>
                  <tbody>
                    {places.map((p) => (
                      <tr key={p.id} className="border-t border-[color:var(--surface-border)]">
                        <td className="px-3 py-2">{p.name}</td>
                        <td className="px-3 py-2 capitalize">{p.place_type}</td>
                        <td className="px-3 py-2 text-[color:var(--text-subtle)]">{p.address || "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
