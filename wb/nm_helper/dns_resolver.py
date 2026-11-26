import socket
import threading
from typing import List


def _disable_dnspython_doh():
    """
    Keep dns.query from importing httpx, which costs ~7 MB of RAM.

    dnspython >= 2.5 imports httpx for DNS-over-HTTPS when python3-httpx is installed, and httpx
    imports rich, click and pygments for its command line tool; we only use plain DNS.
    dnspython decides it once, when dns.query is first imported, so this has to run before the
    imports below. wb_common.mqtt_client does the same for paho-mqtt, but wb-connection-manager
    doesn't import it. Older dnspython has no _features module and is left as is.
    """
    try:
        # pylint: disable-next=import-outside-toplevel
        from dns import _features as dns_features

        dns_features.force("doh", False)
    except (ImportError, AttributeError):
        pass


_disable_dnspython_doh()

# pylint: disable=wrong-import-position
import dns.name  # noqa: E402
import dns.query  # noqa: E402
import dns.resolver  # noqa: E402

# pylint: enable=wrong-import-position


class DomainNameResolveException(Exception):
    pass


_DNS_QUERY_LOCK = threading.Lock()


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

    with _DNS_QUERY_LOCK:
        orig_factory = dns.query.socket_factory

        try:
            dns.query.socket_factory = bound_socket_factory
            answers = resolver.resolve(name, "A")
            return [rdata.to_text() for rdata in answers]
        except Exception as e:
            raise DomainNameResolveException(f"Error during {name} resolving: {e}") from e
        finally:
            dns.query.socket_factory = orig_factory
