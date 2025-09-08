import socket
from typing import List

import dns.resolver


class DomainNameResolveException(Exception):
    pass


def resolve_domain_name(
    name: str,
    iface: str,
    _servers: List[str],
    _domains: List[str],
) -> List[str]:
    resolver = dns.resolver.Resolver()
    resolver.timeout = 2.0
    resolver.lifetime = 6.0

    def bound_socket_factory(af, kind, proto=0):
        sock = socket.socket(af, kind, proto)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, iface.encode() + b"\0")
        return sock

    orig_factory = dns.query.socket_factory

    try:
        dns.query.socket_factory = bound_socket_factory
        answers = resolver.resolve(name, "A")
        return [rdata.to_text() for rdata in answers]
    except Exception as e:
        raise DomainNameResolveException(f"Error during {name} resolving: {e}") from e
    finally:
        dns.query.socket_factory = orig_factory
