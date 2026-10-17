import { useAuth } from "@/context/AuthContext";

/**
 * The dispatch board -- the screen this whole phase exists to deliver.
 *
 * Deliberately a shell for now. The board's real content is live ride requests
 * and the offers sent against them, and building it means reading
 * `apps/rides/serializers.py` and modelling the offer lifecycle properly rather
 * than guessing a shape here and unwinding it later.
 *
 * What it does prove is the plumbing end to end: a signed-in operator, their
 * memberships resolved from `/me/`, and the layout rendering inside the router.
 */
export function DispatchPage() {
  const { user } = useAuth();

  return (
    <>
      <header className="topbar">
        <h1>Dispatch board</h1>
        <span className="muted">
          {user?.memberships.filter((m) => m.is_active).length ?? 0} active
          membership(s)
        </span>
      </header>

      <div className="content stack">
        <div className="card">
          <p style={{ margin: 0 }}>
            Signed in as <strong>{user?.full_name || user?.phone}</strong>.
          </p>
          <p className="muted" style={{ marginBottom: 0 }}>
            The live board — ride requests, driver offers and the accept/decline
            actions — lands here next, against <code>/api/v1/rides/</code>.
          </p>
        </div>

        <div className="card">
          <h2 style={{ fontSize: 14, marginTop: 0 }}>Your access</h2>
          <table>
            <thead>
              <tr>
                <th>Organization</th>
                <th>Role</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {(user?.memberships ?? []).map((membership) => (
                <tr key={membership.id}>
                  <td>{membership.organization_name}</td>
                  <td>{membership.role}</td>
                  <td>
                    <span className="badge">
                      {membership.is_active ? "active" : "inactive"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
