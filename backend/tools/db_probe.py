"""Is a database host reachable from this machine, and over which address family?

A managed PostgreSQL host that resolves to IPv6 only fails from a host without an
IPv6 route, and the error is "Network is unreachable" — which reads like a
firewall or a bad password, and is neither. This separates the three cases that
look alike from the outside: the name does not resolve, the address family is
unreachable, and the port is closed.

    python3 tools/db_probe.py aws-0-eu-central-1.pooler.supabase.com
    python3 tools/db_probe.py db.nsxikvklrbxghmnxnrka.supabase.co

It reports reachability only. It never authenticates, so a password never has to
be passed on a command line or end up in a shell history.
"""

import socket
import sys


def probe(host: str, port: int = 5432) -> int:
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        print(f"{host}:{port} — does not resolve: {exc}")
        return 1

    # getaddrinfo can repeat an address family; keep one entry per family.
    seen = {}
    for family, _, _, _, sockaddr in addresses:
        seen.setdefault(family, sockaddr[0])

    print(f"{host}:{port}")
    if not seen:
        print("  resolves, but to no stream address")
        return 1
    for family, address in sorted(seen.items(), key=lambda item: item[1]):
        print(f"  {'IPv6' if family == socket.AF_INET6 else 'IPv4'}: {address}")

    reachable = False
    for family, _, _, _, sockaddr in addresses:
        family_name = "IPv6" if family == socket.AF_INET6 else "IPv4"
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(8)
        try:
            sock.connect(sockaddr)
        except OSError as exc:
            print(f"  {family_name} connect failed: {type(exc).__name__}: {exc}")
        else:
            print(f"  {family_name} connect ok")
            reachable = True
        finally:
            sock.close()

    if not reachable:
        print(
            "  nothing answered. An IPv6-only host needs an IPv6 route; if the "
            "only address above is IPv6, use the session pooler hostname."
        )
    return 0 if reachable else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    target = sys.argv[1]
    target_port = int(sys.argv[2]) if len(sys.argv) > 2 else 5432
    raise SystemExit(probe(target, target_port))
