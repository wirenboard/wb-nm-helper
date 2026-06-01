from unittest.mock import MagicMock, call, patch

import pytest

from wb.nm_helper import dns_resolver


def test_resolve_domain_name_uses_provided_servers_and_domains():
    resolver = MagicMock()
    resolver.resolve.return_value = [MagicMock(to_text=MagicMock(return_value="1.1.1.1"))]
    original_factory = object()

    with patch.object(dns_resolver.dns.query, "socket_factory", original_factory), patch.object(
        dns_resolver.dns.resolver, "Resolver", return_value=resolver
    ), patch.object(
        dns_resolver.dns.name, "from_text", side_effect=lambda domain: f"name:{domain}"
    ) as from_text:
        result = dns_resolver.resolve_domain_name("host", "eth0", ["8.8.8.8"], ["lan", " corp "])

    assert result == ["1.1.1.1"]
    assert resolver.nameservers == ["8.8.8.8"]
    assert resolver.search == ["name:lan", "name:corp"]
    assert resolver.use_search_by_default is True
    resolver.resolve.assert_called_once_with("host", "A")
    assert from_text.mock_calls == [call("lan"), call("corp")]


def test_resolve_domain_name_keeps_default_resolver_settings_for_empty_servers_and_domains():
    resolver = MagicMock()
    resolver.resolve.return_value = [MagicMock(to_text=MagicMock(return_value="1.1.1.1"))]
    resolver.nameservers = ["system"]
    resolver.search = ["system-domain"]
    resolver.use_search_by_default = False
    original_factory = object()

    with patch.object(dns_resolver.dns.query, "socket_factory", original_factory), patch.object(
        dns_resolver.dns.resolver, "Resolver", return_value=resolver
    ), patch.object(dns_resolver.dns.name, "from_text") as from_text:
        result = dns_resolver.resolve_domain_name("host", "eth0", [], [])

    assert result == ["1.1.1.1"]
    assert resolver.nameservers == ["system"]
    assert resolver.search == ["system-domain"]
    assert resolver.use_search_by_default is False
    resolver.resolve.assert_called_once_with("host", "A")
    from_text.assert_not_called()


def test_resolve_domain_name_binds_socket_to_interface_and_restores_factory():
    fake_socket = MagicMock()

    class ResolverStub:
        def __init__(self):
            self.timeout = None
            self.lifetime = None
            self.nameservers = []

        def resolve(self, *_args, **_kwargs):
            dns_resolver.dns.query.socket_factory(
                dns_resolver.socket.AF_INET, dns_resolver.socket.SOCK_DGRAM, 0
            )
            return [MagicMock(to_text=MagicMock(return_value="1.1.1.1"))]

    resolver = ResolverStub()

    def original_factory(*_args, **_kwargs):
        return None

    with patch.object(dns_resolver.dns.query, "socket_factory", original_factory), patch.object(
        dns_resolver.dns.resolver, "Resolver", return_value=resolver
    ), patch.object(dns_resolver.socket, "socket", return_value=fake_socket) as socket_ctor:
        result = dns_resolver.resolve_domain_name("host", "eth0", ["8.8.8.8"], [])
        assert dns_resolver.dns.query.socket_factory is original_factory

    assert result == ["1.1.1.1"]
    socket_ctor.assert_called_once_with(dns_resolver.socket.AF_INET, dns_resolver.socket.SOCK_DGRAM, 0)
    fake_socket.setsockopt.assert_called_once_with(
        dns_resolver.socket.SOL_SOCKET,
        dns_resolver.socket.SO_BINDTODEVICE,
        b"eth0\0",
    )


def test_resolve_domain_name_restores_factory_when_resolve_raises():
    fake_socket = MagicMock()

    class ResolverStub:
        def __init__(self):
            self.timeout = None
            self.lifetime = None
            self.nameservers = []

        def resolve(self, *_args, **_kwargs):
            dns_resolver.dns.query.socket_factory(
                dns_resolver.socket.AF_INET, dns_resolver.socket.SOCK_DGRAM, 0
            )
            raise RuntimeError("boom")

    resolver = ResolverStub()

    def original_factory(*_args, **_kwargs):
        return None

    with patch.object(dns_resolver.dns.query, "socket_factory", original_factory), patch.object(
        dns_resolver.dns.resolver, "Resolver", return_value=resolver
    ), patch.object(dns_resolver.socket, "socket", return_value=fake_socket):
        with pytest.raises(dns_resolver.DomainNameResolveException, match="Error during host resolving"):
            dns_resolver.resolve_domain_name("host", "eth0", [], [])
        assert dns_resolver.dns.query.socket_factory is original_factory

    fake_socket.setsockopt.assert_called_once_with(
        dns_resolver.socket.SOL_SOCKET,
        dns_resolver.socket.SO_BINDTODEVICE,
        b"eth0\0",
    )
