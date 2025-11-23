import io
import logging
from typing import List
from urllib.parse import urlparse

import pycurl

from wb.nm_helper.dns_resolver import DomainNameResolveException, resolve_domain_name

CONNECTIVITY_CHECK_TIMEOUT = 15


def get_host_name(url: str) -> str:
    parsed_url = urlparse(url)
    return parsed_url.hostname if parsed_url.hostname is not None else url


def set_curl_url_and_host(curl, url: str, host: str) -> None:
    curl.setopt(curl.URL, url)
    curl.setopt(curl.HTTPHEADER, [f"Host: {host}"])


def set_curl_opt(curl, url: str, host_ip: str) -> None:
    if host_ip is None:
        set_curl_url_and_host(curl, url, url)
        return
    parsed_url = urlparse(url)
    if not parsed_url.hostname:
        set_curl_url_and_host(curl, url, url)
        return
    port = parsed_url.port
    if port is None:
        port = "443" if parsed_url.scheme == "https" else "80"
    resolve = [f"{parsed_url.hostname}:{port}:{host_ip}"]
    logging.debug("libcurl resolve opt %s", resolve)
    curl.setopt(curl.RESOLVE, resolve)
    set_curl_url_and_host(curl, url, parsed_url.hostname)


def curl_get(iface: str, url: str, host_ip: str) -> str:
    buffer = io.BytesIO()
    curl = pycurl.Curl()
    set_curl_opt(curl, url, host_ip)
    curl.setopt(curl.WRITEDATA, buffer)
    curl.setopt(curl.INTERFACE, iface)
    curl.setopt(pycurl.CONNECTTIMEOUT, CONNECTIVITY_CHECK_TIMEOUT)
    curl.setopt(pycurl.TIMEOUT, CONNECTIVITY_CHECK_TIMEOUT)
    curl.perform()
    curl.close()
    return buffer.getvalue().decode("UTF-8")


class ConnectionChecker:  # pylint: disable=R0903
    def __init__(self, dns_resolver_fn=None):
        self._dns_resolver_fn = resolve_domain_name if dns_resolver_fn is None else dns_resolver_fn
        self._last_address = None

    def _check_url(self, iface: str, url: str, host_ip: str, expected_payload: str) -> bool:
        payload = curl_get(iface, url, host_ip)
        logging.debug("Payload is %s", payload)
        answer_is_ok = expected_payload in payload
        logging.debug("Connectivity via %s is %s", iface, answer_is_ok)
        return answer_is_ok

    def _check_addresses(self, iface: str, url: str, addresses: List[str], expected_payload: str) -> bool:
        for address in addresses:
            try:
                if address != self._last_address:
                    check_result = self._check_url(iface, url, address, expected_payload)
                    self._last_address = address
                    return check_result
            except pycurl.error as ex:
                logging.debug("Error during %s connectivity check: %s", iface, ex)
        return False

    def _get_addresses(
        self,
        iface: str,
        url: str,
        servers: List[str],
        domains: List[str],
    ) -> List[str]:
        hostname = get_host_name(url)
        logging.debug(
            "Resolve %s via %s using DNS servers: %s, DNS search domains: %s",
            hostname,
            iface,
            servers,
            domains,
        )
        addresses = self._dns_resolver_fn(hostname, iface, servers, domains)
        logging.debug("%s resolves to %s", hostname, addresses)
        return addresses

    def check(  # pylint: disable=R0913 disable=R0917
        self,
        iface: str,
        url: str,
        expected_payload: str,
        servers: List[str] = None,
        domains: List[str] = None,
    ) -> bool:
        try:
            if self._last_address:
                return self._check_url(iface, url, self._last_address, expected_payload)
        except pycurl.error as ex:
            logging.debug("Error during %s connectivity check: %s", iface, ex)

        if servers is None:
            servers = []

        if domains is None:
            domains = []

        addresses = []
        try:
            addresses = self._get_addresses(iface, url, servers, domains)
        except DomainNameResolveException as ex:
            logging.debug("Error during %s connectivity check: %s", iface, ex)
            return False

        return self._check_addresses(iface, url, addresses, expected_payload)
