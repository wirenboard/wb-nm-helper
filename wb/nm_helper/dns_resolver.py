import socket
from typing import List

import dns.name
import dns.query
import dns.resolver


class DomainNameResolveException(Exception):
    pass


def resolve_domain_name(
    name: str,
    iface: str,
    servers: List[str],
    domains: List[str],
) -> List[str]:
    resolver = dns.resolver.Resolver()
    resolver.timeout = 2.0
    resolver.lifetime = 6.0

    nameservers = [server for server in servers if server]
    if nameservers:
        resolver.nameservers = nameservers

    search_domains = [domain.strip() for domain in domains if domain and domain.strip()]
    if search_domains:
        resolver.search = [dns.name.from_text(domain) for domain in search_domains]
        resolver.use_search_by_default = True

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
