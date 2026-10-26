import {
  Bell,
  Bus,
  Building2,
  Car,
  ChevronDown,
  LayoutDashboard,
  LifeBuoy,
  LogIn,
  LogOut,
  Package,
  Radio,
  Receipt,
  ScrollText,
  ShieldCheck,
  Store,
  UserCircle2,
  Users,
  Wallet,
  Warehouse,
  Landmark,
} from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { useAuth } from "@/context/AuthContext";
import { cn } from "@/lib/utils";
import { useClickOutside } from "@/hooks/use-click-outside";

type NavEntry = {
  to: string;
  label: string;
  icon: React.ReactNode;
  planned?: boolean;
};

type NavSection = {
  heading: string;
  entries: NavEntry[];
};

function buildSections({
  canSeeStaff,
  canSeeFleet,
  canSellAtCounter,
  isOrgAdmin,
}: {
  canSeeStaff: boolean;
  canSeeFleet: boolean;
  canSellAtCounter: boolean;
  isOrgAdmin: boolean;
}): NavSection[] {
  return [
    {
      heading: "",
      entries: [
        { to: "/dispatch", label: "Overview", icon: <LayoutDashboard className="h-[18px] w-[18px]" /> },
      ],
    },
    {
      heading: "Operations",
      entries: [
        { to: "/dispatch/board", label: "Dispatch board", icon: <Radio className="h-[18px] w-[18px]" /> },
        canSellAtCounter
          ? { to: "/counter", label: "Counter sales", icon: <Store className="h-[18px] w-[18px]" /> }
          : { to: "/counter", label: "Counter sales", icon: <Store className="h-[18px] w-[18px]" />, planned: true },
        { to: "/passenger", label: "Passenger", icon: <Bus className="h-[18px] w-[18px]" /> },
        { to: "/goods", label: "Goods and hubs", icon: <Package className="h-[18px] w-[18px]" /> },
        { to: "/branches", label: "Branches", icon: <Warehouse className="h-[18px] w-[18px]" /> },
      ],
    },
    {
      heading: "Resources",
      entries: [
        canSeeFleet
          ? { to: "/setup/fleet", label: "Fleet", icon: <Car className="h-[18px] w-[18px]" /> }
          : { to: "/dispatch", label: "Fleet", icon: <Car className="h-[18px] w-[18px]" />, planned: true },
        isOrgAdmin
          ? { to: "/setup/routes", label: "Routes", icon: <Radio className="h-[18px] w-[18px]" /> }
          : { to: "/dispatch", label: "Routes", icon: <Radio className="h-[18px] w-[18px]" />, planned: true },
        canSeeStaff
          ? { to: "/people", label: "People", icon: <Users className="h-[18px] w-[18px]" /> }
          : { to: "/people", label: "People", icon: <Users className="h-[18px] w-[18px]" />, planned: true },
      ],
    },
    {
      heading: "Finance",
      entries: [
        { to: "/finance/statements", label: "Statements", icon: <Receipt className="h-[18px] w-[18px]" /> },
        { to: "/finance/collections", label: "Collections", icon: <Wallet className="h-[18px] w-[18px]" /> },
        { to: "/finance/payouts", label: "Payouts", icon: <Landmark className="h-[18px] w-[18px]" /> },
      ],
    },
    {
      heading: "Support",
      entries: [
        { to: "/support/cases", label: "Cases", icon: <LifeBuoy className="h-[18px] w-[18px]" /> },
        { to: "/support/safety", label: "Safety and appeals", icon: <ShieldCheck className="h-[18px] w-[18px]" /> },
      ],
    },
    ...(isOrgAdmin
      ? [
          {
            heading: "Administration",
            entries: [
              { to: "/setup/company", label: "Organisations", icon: <Building2 className="h-[18px] w-[18px]" /> },
              { to: "/admin/audit", label: "Audit log", icon: <ScrollText className="h-[18px] w-[18px]" /> },
            ],
          },
        ]
      : []),
  ];
}

function NavRow({
  entry,
  onClick,
}: {
  entry: NavEntry;
  onClick?: () => void;
}) {
  const base =
    "group relative flex items-center gap-3 rounded-lg px-3 py-2 text-[13px] font-medium transition-all duration-150 min-h-[40px]";

  if (entry.planned) {
    return (
      <NavLink
        to={entry.to}
        onClick={onClick}
        className={({ isActive }) =>
          cn(
            base,
            isActive
              ? "bg-white/10 text-white"
              : "text-white/60 hover:bg-white/[0.06] hover:text-white"
          )
        }
      >
        {({ isActive }) => (
          <>
            {isActive && (
              <span className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-[#2DD4BF]" />
            )}
            <span className={cn("transition-colors", isActive ? "text-[#2DD4BF]" : "text-white/45 group-hover:text-white/70")}>
              {entry.icon}
            </span>
            <span className="truncate">{entry.label}</span>
          </>
        )}
      </NavLink>
    );
  }

  return (
    <NavLink
      to={entry.to}
      onClick={onClick}
      className={({ isActive }) =>
        cn(
          base,
          isActive
            ? "bg-white/10 text-white"
            : "text-white/60 hover:bg-white/[0.06] hover:text-white"
        )
      }
    >
      {({ isActive }) => (
        <>
          {isActive && (
            <span className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-[#2DD4BF]" />
          )}
          <span className={cn("transition-colors", isActive ? "text-[#2DD4BF]" : "text-white/45 group-hover:text-white/70")}>
            {entry.icon}
          </span>
          <span className="truncate">{entry.label}</span>
        </>
      )}
    </NavLink>
  );
}

function SidebarBody({ onNavigate, collapsed = false, onToggle }: { onNavigate?: () => void; collapsed?: boolean; onToggle?: () => void }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useClickOutside<HTMLDivElement>(() => setMenuOpen(false));

  const primaryMembership = user?.memberships.find((m) => m.is_active) ?? null;
  const canSeeStaff = (user?.memberships ?? []).some(
    (m) =>
      m.is_active &&
      ["operator_admin", "platform_admin", "branch_manager"].includes(m.role)
  );
  const canSellAtCounter = (user?.memberships ?? []).some(
    (m) =>
      m.is_active &&
      ["branch_staff", "branch_manager", "operator_admin", "platform_admin"].includes(m.role)
  );
  const isOrgAdmin = (user?.memberships ?? []).some(
    (m) => m.is_active && ["operator_admin", "platform_admin"].includes(m.role)
  );
  const canSeeFleet = (user?.memberships ?? []).some(
    (m) =>
      m.is_active &&
      ["operator_admin", "platform_admin", "branch_manager"].includes(m.role)
  );
  const sections = buildSections({
    canSeeStaff,
    canSeeFleet,
    canSellAtCounter,
    isOrgAdmin,
  });

  function handleLogout() {
    logout();
    navigate("/login", { replace: true });
  }

  return (
    <>
      <div className={cn("flex items-center gap-2.5 h-[68px] border-b border-white/[0.07] shrink-0", collapsed ? "justify-center px-2" : "px-5")}>
        <div className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-[#0E7C6F] to-[#2DD4BF] shadow-lg shadow-[#0E7C6F]/30">
          <LogIn className="h-4 w-4 text-white" />
        </div>
        {!collapsed && (
          <div className="min-w-0 flex-1">
            <div className="text-[15px] font-bold text-white leading-tight tracking-tight">
              Transport Ops
            </div>
            <div className="text-[10px] font-medium uppercase tracking-[0.12em] text-white/35">
              Kenya Platform
            </div>
          </div>
        )}
        {onToggle && !collapsed && (
          <button onClick={onToggle} className="text-white/40 hover:text-white">
            <ChevronDown className="h-4 w-4 -rotate-90" />
          </button>
        )}
      </div>

      <nav className="flex-1 overflow-y-auto overflow-x-hidden px-2 py-4 space-y-5 scrollbar-thin">
        {sections.map((section, index) => (
          <div key={section.heading || `section-${index}`}>
            {section.heading && (
              <div className="px-3 pb-2 text-[10px] font-bold uppercase tracking-[0.12em] text-white/30">
                {section.heading}
              </div>
            )}
            <div className="space-y-0.5">
              {section.entries.map((entry) => (
                <NavRow
                  key={`${section.heading}-${entry.label}`}
                  entry={entry}
                  onClick={onNavigate}
                />
              ))}
            </div>
          </div>
        ))}
      </nav>

      <div className="border-t border-white/[0.07] p-3">
        <div className="relative" ref={menuRef}>
          <button
            onClick={() => setMenuOpen((v) => !v)}
            className="w-full flex items-center gap-3 rounded-lg px-2 py-2 text-left transition hover:bg-white/[0.06]"
          >
            <div className="grid h-9 w-9 place-items-center rounded-full bg-white/10">
              <UserCircle2 className="h-5 w-5 text-white/70" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium text-white truncate">
                {user?.full_name || user?.phone || "Account"}
              </div>
              <div className="text-xs text-white/40 truncate">
                {primaryMembership?.role?.replace("_", " ") || "Signed in"}
              </div>
            </div>
            <ChevronDown className={cn("h-4 w-4 text-white/40 transition", menuOpen && "rotate-180")} />
          </button>

          {menuOpen && (
            <div className="absolute bottom-full left-0 right-0 mb-2 rounded-xl bg-[#0F1F2C] border border-white/10 shadow-2xl overflow-hidden">
              <button
                onClick={handleLogout}
                className="w-full flex items-center gap-2 px-3 py-2 text-sm text-white/70 hover:bg-white/[0.06] hover:text-white"
              >
                <LogOut className="h-4 w-4" />
                Sign out
              </button>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

export function AppLayout() {
  const { user } = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className="min-h-screen bg-[color:var(--bg)] text-[color:var(--text)]">
      <div className="flex min-h-screen">
        <aside
          className={cn(
            "hidden lg:flex lg:flex-col bg-gradient-to-b from-[#0B1621] to-[#0F1F2C] text-white border-r border-white/[0.06] transition-all duration-200 h-screen sticky top-0 overflow-hidden",
            collapsed ? "w-[76px]" : "w-[280px]"
          )}
        >
          <SidebarBody collapsed={collapsed} onToggle={() => setCollapsed((v) => !v)} />
        </aside>

        {mobileOpen && (
          <div className="fixed inset-0 z-40 lg:hidden">
            <div className="absolute inset-0 bg-black/60" onClick={() => setMobileOpen(false)} />
            <aside className="absolute left-0 top-0 h-full w-[280px] bg-gradient-to-b from-[#0B1621] to-[#0F1F2C] text-white flex flex-col">
              <SidebarBody onNavigate={() => setMobileOpen(false)} />
            </aside>
          </div>
        )}

        <div className="flex-1 flex flex-col min-w-0">
          <header className="sticky top-0 z-30 flex items-center justify-between gap-3 px-4 sm:px-6 h-[68px] bg-[color:var(--surface)]/95 backdrop-blur border-b border-[color:var(--surface-border)]">
            <div className="flex items-center gap-2">
              <button
                onClick={() => setMobileOpen(true)}
                className="lg:hidden inline-flex items-center justify-center h-9 w-9 rounded-lg border border-[color:var(--surface-border)] hover:bg-[color:var(--surface-subtle)]"
              >
                <span className="sr-only">Open menu</span>
                <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
                  <path d="M3 5h12M3 9h12M3 13h12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
              </button>
              <div className="hidden sm:flex items-center gap-2 text-sm text-[color:var(--text-subtle)]">
                <Bell className="h-4 w-4" />
                <span>Operations console</span>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <span className="hidden md:inline-flex items-center rounded-full px-2.5 py-1 text-xs border border-[color:var(--surface-border)] text-[color:var(--text-subtle)]">
                {primaryRole(user)}
              </span>
            </div>
          </header>
          <main className="flex-1">
            <Outlet />
          </main>
        </div>
      </div>
    </div>
  );
}

function primaryRole(user: any) {
  const m = user?.memberships?.find((x: any) => x.is_active);
  return m?.role ? m.role.replace("_", " ") : "User";
}
