import select
import socket
from typing import List

import pycares


# Taken from https://github.com/saghul/pycares/blob/master/examples/cares-select.py
def wait_pycares_channel(channel: pycares.Channel) -> None:
    while True:
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


def resolve_domain_name(name: str, iface: str) -> List[str]:
    # From c-ares docs:
    # timeout - the number of seconds each name server is given to respond to a query on the first try.
    # tries - the number of tries the resolver will try contacting each name server before giving up.
    # After the first try, the timeout algorithm becomes more complicated,
    # but scales linearly with the value of timeout.
    #
    # Actually it multiplies timeout by 2 for every try.
    # According to the settings it is 2, 4 and 8 seconds
    channel = pycares.Channel(tries=3, timeout=2)
    channel.set_local_dev(iface.encode())
    callback = PycaresCallback()
    channel.gethostbyname(name, socket.AF_INET, callback)
    wait_pycares_channel(channel)
    if callback.error is None and callback.result is not None:
        return callback.result.addresses
    raise DomainNameResolveException(
        f"Error during {name} resolving: "
        + ("can't get address" if callback.error is None else pycares.errno.strerror(callback.error))
    )
