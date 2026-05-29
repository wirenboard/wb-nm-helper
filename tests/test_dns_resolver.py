from unittest.mock import MagicMock, call, patch

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
