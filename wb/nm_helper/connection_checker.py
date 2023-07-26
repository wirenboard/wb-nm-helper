import io
import logging
from typing import List
from urllib.parse import urlparse, urlunparse

import pycurl

from wb.nm_helper.dns_resolver import DomainNameResolveException, resolve_domain_name

CONNECTIVITY_CHECK_TIMEOUT = 15


def replace_host_name_with_ip(url: str, ip: str) -> str:
    if ip is None:
        return url
    parsed_url = urlparse(url)
    if not parsed_url.hostname:
        return url
    if parsed_url.port is not None:
        return urlunparse(parsed_url._replace(netloc="{}:{}".format(ip, parsed_url.port)))
    return urlunparse(parsed_url._replace(netloc=ip))


def get_host_name(url: str) -> str:
    parsed_url = urlparse(url)
    return parsed_url.hostname if parsed_url.hostname is not None else url


def curl_get(iface: str, url: str, ip: str) -> str:
    buffer = io.BytesIO()
    curl = pycurl.Curl()
    curl.setopt(curl.URL, replace_host_name_with_ip(url, ip))
    curl.setopt(curl.WRITEDATA, buffer)
    curl.setopt(curl.INTERFACE, iface)
    curl.setopt(pycurl.CONNECTTIMEOUT, CONNECTIVITY_CHECK_TIMEOUT)
    curl.setopt(pycurl.TIMEOUT, CONNECTIVITY_CHECK_TIMEOUT)
    curl.setopt(curl.HTTPHEADER, ["Host: {}".format(get_host_name(url))])
    curl.perform()
    curl.close()
    return buffer.getvalue().decode("UTF-8")


class ConnectionChecker:
    def __init__(self, dns_resolver_fn=None):
        self.dns_resolver_fn = resolve_domain_name if dns_resolver_fn is None else dns_resolver_fn
        self.last_address = None

    def _check_url(self, iface: str, url: str, ip: str, expected_payload: str) -> bool:
        payload = curl_get(iface, url, ip)
        logging.debug("Payload is %s", payload)
        answer_is_ok = expected_payload in payload
        logging.debug("Connectivity via %s is %s", iface, answer_is_ok)
        return answer_is_ok

    def _check_addresses(self, iface: str, url: str, addresses: List[str], expected_payload: str) -> bool:
        for address in addresses:
            try:
                if address != self.last_address:
                    check_result = self._check_url(iface, url, address, expected_payload)
                    self.last_address = address
                    return check_result
            except pycurl.error as ex:
                logging.debug("Error during %s connectivity check: %s", iface, ex)
        return False

    def _get_addresses(self, iface: str, url: str) -> List[str]:
        hostname = get_host_name(url)
        addresses = self.dns_resolver_fn(iface, hostname)
        logging.debug("%s resolves to %s", hostname, addresses)
        return addresses

    def check(self, iface: str, url: str, expected_payload: str) -> bool:
        try:
            if self.last_address:
                return self._check_url(iface, url, self.last_address, expected_payload)
        except pycurl.error as ex:
            logging.debug("Error during %s connectivity check: %s", iface, ex)

        addresses = []
        try:
            addresses = self._get_addresses(iface, url)
        except DomainNameResolveException as ex:
            logging.debug("Error during %s connectivity check: %s", iface, ex)
            return False

        return self._check_addresses(iface, url, addresses, expected_payload)
