import io
from unittest import TestCase
from unittest.mock import MagicMock, call, patch

import pycurl

from wb.nm_helper import connection_checker
from wb.nm_helper.dns_resolver import DomainNameResolveException, resolve_domain_name

# DUMMY CLASSES


class DummyCurl:  # pylint: disable=R0903
    URL = 10001
    WRITEDATA = 10002
    INTERFACE = 10003
    HTTPHEADER = 10023


class DummyBytesIO:  # pylint: disable=R0903
    pass


# TESTS


class ConnectionCheckerSingleFunctionTests(TestCase):
    def test_curl_get(self):
        DummyCurl.setopt = MagicMock()
        DummyCurl.perform = MagicMock()
        DummyCurl.close = MagicMock()
        DummyBytesIO.getvalue = MagicMock(return_value="ЖЖЖ".encode("UTF8"))
        with patch.object(pycurl, "Curl", DummyCurl), patch.object(io, "BytesIO", DummyBytesIO):
            output = connection_checker.curl_get("dummy_if", "http://good_url.com/params/some", "1.1.1.1")
            self.assertEqual(6, DummyCurl.setopt.call_count)
            self.assertEqual(
                call(pycurl.Curl.URL, "http://1.1.1.1/params/some"), DummyCurl.setopt.mock_calls[0]
            )
            self.assertEqual(2, len(DummyCurl.setopt.mock_calls[1].args))
            self.assertEqual(pycurl.Curl.WRITEDATA, DummyCurl.setopt.mock_calls[1].args[0])
            self.assertTrue(isinstance(DummyCurl.setopt.mock_calls[1].args[1], DummyBytesIO))
            self.assertEqual(call(pycurl.Curl.INTERFACE, "dummy_if"), DummyCurl.setopt.mock_calls[2])
            self.assertEqual(
                call(pycurl.CONNECTTIMEOUT, connection_checker.CONNECTIVITY_CHECK_TIMEOUT),
                DummyCurl.setopt.mock_calls[3],
            )
            self.assertEqual(
                call(pycurl.TIMEOUT, connection_checker.CONNECTIVITY_CHECK_TIMEOUT),
                DummyCurl.setopt.mock_calls[4],
            )
            self.assertEqual(
                call(pycurl.Curl.HTTPHEADER, ["Host: good_url.com"]),
                DummyCurl.setopt.mock_calls[5],
            )
            self.assertEqual([call()], DummyCurl.perform.mock_calls)
            self.assertEqual([call()], DummyCurl.close.mock_calls)
            self.assertEqual("ЖЖЖ", output)

    def test_get_host_name_with_ip(self):
        self.assertEqual(
            "good_url.com",
            connection_checker.get_host_name("http://good_url.com/no/ip"),
        )
        self.assertEqual("bad_url", connection_checker.get_host_name("bad_url"))

    def test_replace_host_name_with_ip(self):
        self.assertEqual(
            "http://good_url.com/no/ip",
            connection_checker.replace_host_name_with_ip("http://good_url.com/no/ip", None),
        )
        self.assertEqual("bad_url", connection_checker.replace_host_name_with_ip("bad_url", "1.1.1.1"))
        self.assertEqual(
            "http://1.1.1.1/params/some",
            connection_checker.replace_host_name_with_ip("http://good_url.com/params/some", "1.1.1.1"),
        )
        self.assertEqual(
            "http://1.1.1.1:8080/params/some",
            connection_checker.replace_host_name_with_ip("http://good_url.com:8080/params/some", "1.1.1.1"),
        )


class ConnectionCheckerTests(TestCase):
    def test_init(self):
        checker = connection_checker.ConnectionChecker()
        self.assertEqual(resolve_domain_name, checker._dns_resolver_fn)  # pylint: disable=W0212

    def test_check_first_time_one_ip(self):
        dns_resolver_mock = MagicMock()
        dns_resolver_mock.return_value = ["1.1.1.1"]
        checker = connection_checker.ConnectionChecker(dns_resolver_mock)
        with patch.object(connection_checker, "curl_get") as mock_curl_get:
            mock_curl_get.return_value = "payload"
            self.assertEqual(True, checker.check("eth0", "http://good_url.com/params/some", "payload"))
            self.assertEqual([call("eth0", "good_url.com")], dns_resolver_mock.mock_calls)
            self.assertEqual(
                [call("eth0", "http://good_url.com/params/some", "1.1.1.1")], mock_curl_get.mock_calls
            )

    def test_check_first_time_several_ips(self):
        dns_resolver_mock = MagicMock()
        dns_resolver_mock.return_value = ["1.1.1.1", "2.2.2.2"]
        checker = connection_checker.ConnectionChecker(dns_resolver_mock)
        with patch.object(connection_checker, "curl_get") as mock_curl_get:

            def curl_get_side_effect_fn(_iface: str, _url: str, host_ip: str) -> str:
                if host_ip == "1.1.1.1":
                    raise pycurl.error()
                return "payload"

            mock_curl_get.side_effect = curl_get_side_effect_fn
            self.assertEqual(True, checker.check("eth0", "http://good_url.com/params/some", "payload"))
            self.assertEqual([call("eth0", "good_url.com")], dns_resolver_mock.mock_calls)
            self.assertEqual(
                [
                    call("eth0", "http://good_url.com/params/some", "1.1.1.1"),
                    call("eth0", "http://good_url.com/params/some", "2.2.2.2"),
                ],
                mock_curl_get.mock_calls,
            )

    def test_check_first_time_resolve_exception(self):
        dns_resolver_mock = MagicMock()
        dns_resolver_mock.side_effect = DomainNameResolveException()
        checker = connection_checker.ConnectionChecker(dns_resolver_mock)
        with patch.object(connection_checker, "curl_get") as mock_curl_get:
            mock_curl_get.return_value = "payload"
            self.assertEqual(False, checker.check("eth0", "http://good_url.com/params/some", "payload"))
            self.assertEqual([call("eth0", "good_url.com")], dns_resolver_mock.mock_calls)
            self.assertEqual([], mock_curl_get.mock_calls)

    def test_check_first_time_one_ip_curl_exception(self):
        dns_resolver_mock = MagicMock()
        dns_resolver_mock.return_value = ["1.1.1.1"]
        checker = connection_checker.ConnectionChecker(dns_resolver_mock)
        with patch.object(connection_checker, "curl_get") as mock_curl_get:
            mock_curl_get.side_effect = pycurl.error()
            self.assertEqual(False, checker.check("eth0", "http://good_url.com/params/some", "payload"))
            self.assertEqual([call("eth0", "good_url.com")], dns_resolver_mock.mock_calls)
            self.assertEqual(
                [call("eth0", "http://good_url.com/params/some", "1.1.1.1")], mock_curl_get.mock_calls
            )

    def test_check_cached_ip(self):
        dns_resolver_mock = MagicMock()
        checker = connection_checker.ConnectionChecker(dns_resolver_mock)
        with patch.object(connection_checker, "curl_get") as mock_curl_get:
            # First time resolve is ok
            mock_curl_get.return_value = "payload"
            dns_resolver_mock.return_value = ["1.1.1.1"]
            self.assertEqual(True, checker.check("eth0", "http://good_url.com/params/some", "payload"))
            self.assertEqual([call("eth0", "good_url.com")], dns_resolver_mock.mock_calls)
            self.assertEqual(
                [call("eth0", "http://good_url.com/params/some", "1.1.1.1")], mock_curl_get.mock_calls
            )

            # Next time try get from known ip without dns request
            dns_resolver_mock.reset_mock()
            mock_curl_get.reset_mock()
            mock_curl_get.return_value = "payload"
            dns_resolver_mock.return_value = ["1.1.1.1"]
            self.assertEqual(True, checker.check("eth0", "http://good_url.com/params/some", "payload"))
            self.assertEqual([], dns_resolver_mock.mock_calls)
            self.assertEqual(
                [call("eth0", "http://good_url.com/params/some", "1.1.1.1")], mock_curl_get.mock_calls
            )

            # Next time known ip is not responding, send dns request and get new ip
            dns_resolver_mock.reset_mock()
            mock_curl_get.reset_mock()

            def curl_get_side_effect_fn(_iface: str, _url: str, host_ip: str) -> str:
                if host_ip == "1.1.1.1":
                    raise pycurl.error()
                return "payload"

            mock_curl_get.side_effect = curl_get_side_effect_fn
            dns_resolver_mock.return_value = ["2.2.2.2"]
            self.assertEqual(True, checker.check("eth0", "http://good_url.com/params/some", "payload"))
            self.assertEqual([call("eth0", "good_url.com")], dns_resolver_mock.mock_calls)
            self.assertEqual(
                [
                    call("eth0", "http://good_url.com/params/some", "1.1.1.1"),
                    call("eth0", "http://good_url.com/params/some", "2.2.2.2"),
                ],
                mock_curl_get.mock_calls,
            )
