import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { useAuth } from "@/context/AuthContext";

/**
 * The application shell: a fixed sidebar and a routed content pane.
 *
 * Navigation is declared here rather than per page so the operator sees the same
 * map everywhere. Sections whose API does not exist yet are simply absent --
 * a nav link to a dead route is worse than no link.
 */

interface NavEntry {
  to: string;
  label: string;
}

const OPERATIONS: NavEntry[] = [
  { to: "/dispatch", label: "Dispatch board" },
];

const GOODS: NavEntry[] = [
  { to: "/freight-trips", label: "Freight trips" },
  { to: "/shipments", label: "Shipments" },
];

function SidebarLink({ entry }: { entry: NavEntry }) {
  return (
    <NavLink
      to={entry.to}
      className={({ isActive }) =>
        isActive ? "nav-item nav-item--active" : "nav-item"
      }
    >
      {entry.label}
    </NavLink>
  );
}

export function AppLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  function handleLogout() {
    logout();
    navigate("/login", { replace: true });
  }

  const primaryMembership = user?.memberships.find((m) => m.is_active) ?? null;

  return (
    <div className="shell">
      <nav className="sidebar">
        <div className="sidebar__brand">Transport Ops</div>

        <div className="sidebar__section">Operations</div>
        {OPERATIONS.map((entry) => (
          <SidebarLink key={entry.to} entry={entry} />
        ))}

        <div className="sidebar__section">Goods</div>
        {GOODS.map((entry) => (
          <SidebarLink key={entry.to} entry={entry} />
        ))}

        <div className="sidebar__footer">
          <div className="who">
            <strong>{user?.full_name || user?.phone}</strong>
            {primaryMembership
              ? `${primaryMembership.organization_name} — ${primaryMembership.role}`
              : "No active membership"}
          </div>
          <button type="button" className="link" onClick={handleLogout}>
            Sign out
          </button>
        </div>
      </nav>

      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
