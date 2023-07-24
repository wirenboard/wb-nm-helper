import select
import socket
import time

import pycares


# Taken from https://github.com/saghul/pycares/blob/master/examples/cares-select.py
def wait_pycares_channel(channel: pycares.Channel) -> None:
    # Calc time enough to ask every server
    timeout = len(channel.servers) * 5000000000 + 1000000000
    start = time.monotonic_ns()
    while time.monotonic_ns() - start < timeout:
        read_fds, write_fds = channel.getsock()
        if not read_fds and not write_fds:
            break
        timeout = channel.timeout()
        no_file_d = pycares.ARES_SOCKET_BAD  # pylint: disable=E1101
        if not timeout:
            channel.process_fd(no_file_d, no_file_d)
            continue
        rlist, wlist, _xlist = select.select(read_fds, write_fds, [], timeout)
        for file_d in rlist:
            channel.process_fd(file_d, no_file_d)
        for file_d in wlist:
            channel.process_fd(no_file_d, file_d)


class DomainNameResolveException(Exception):
    pass


class PycaresCallback:  # pylint: disable=R0903
    def __init__(self) -> None:
        self.result = None
        self.error = None

    def __call__(self, result, error) -> None:
        self.result = result
        self.error = error


def resolve_domain_name(name: str, iface: str) -> str:
    channel = pycares.Channel()
    channel.set_local_dev(iface.encode())
    callback = PycaresCallback()
    channel.gethostbyname(name, socket.AF_INET, callback)
    wait_pycares_channel(channel)
    if callback.error is None and callback.result is not None and len(callback.result.addresses) > 0:
        return callback.result.addresses[0]
    raise DomainNameResolveException(
        "Error during {} resolving: {}".format(name, "timeout" if callback.error is None else callback.error)
    )
