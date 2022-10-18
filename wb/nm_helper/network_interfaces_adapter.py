#!/usr/bin/env python
# -*- mode: python; coding: utf-8 -*-
#
# Based on https://github.com/privacyidea/networkparser
# The original license follows:
#
# The MIT License (MIT)
#
# Copyright (c) 2015 Cornelius Koelbel
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of
# this software and associated documentation files (the "Software"), to deal in
# the Software without restriction, including without limitation the rights to
# use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
# the Software, and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
# FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
# COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
# IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.


from os.path import exists

from pyparsing import (
    CharsNotIn,
    Forward,
    Group,
    Literal,
    OneOrMore,
    Optional,
    Regex,
    SkipTo,
    White,
    Word,
    ZeroOrMore,
    alphanums,
    pythonStyleComment,
)

from .network_managing_system import INetworkManagingSystem

NETWORK_INTERFACES_CONFIG = "/etc/network/interfaces"


class NetworkInterfacesAdapter(INetworkManagingSystem):

    interface = Word(alphanums + ":")
    key = Word(alphanums + "-_")
    space = White().suppress()
    value = CharsNotIn("{}\n#")
    line = Regex("^.*$")
    comment = "#"
    method = Regex("loopback|manual|dhcp|static|ppp|bootp|tunnel|wvdial|ipv4ll")
    stanza = Regex("auto|iface|mapping|allow-hotplug")
    option_key = Regex(
        "bridge_\\w*|post-\\w*|up|down|pre-\\w*|address"
        "|network|netmask|gateway|broadcast|dns-\\w*|scope|"
        "pointtopoint|metric|hwaddress|mtu|hostname|"
        "leasehours|leasetime|vendor|client|bootfile|server"
        "|mode|endpoint|dstaddr|local|ttl|provider|unit"
        "|options|frame|bitrate|netnum|media|wpa-[\\w-]*"
    )
    _eol = Literal("\n").suppress()
    option = Forward()
    option <<= Group(space + option_key + space + SkipTo(_eol))
    interface_block = Forward()
    interface_block <<= Group(
        stanza + space + interface + Optional(space + Regex("inet|can") + method + Group(ZeroOrMore(option)))
    )

    interface_file = OneOrMore(interface_block).ignore(pythonStyleComment)

    def __init__(self, input_file_name=None, content=None):
        self.filename = None
        self.content = None
        if content:
            self.content = content
        elif input_file_name is not None:
            self.filename = input_file_name
            self._read()
        if self.content is not None:
            self.interfaces = self.get_interfaces()

    def _read(self):
        """
        Reread the contents from the disk
        """
        with open(self.filename, "r", encoding="utf-8") as file:
            self.content = file.read()

    def get(self):
        """
        return the grouped config
        """
        if self.filename:
            self._read()
        if len(self.content):
            return self.interface_file.parseString(self.content)
        return []

    def format(self):
        """
        Format the single interfaces e.g. for writing to a file.

        [
            {
              "auto": True,
              "method": "static",
              "options": {
                "address": "1.1.1.1",
                "netmask": "255.255.255.0"
              }
            }
        ]
        results in

        auto eth0
        iface eth0 inet static
          address 1.1.1.1
          netmask 255.255.255.0

        :return: string
        """
        output = ""
        for iface in self.interfaces:
            name = iface["name"]
            if iface.get("auto"):
                output += "auto %s\n" % name
            if iface.get("allow-hotplug"):
                output += "allow-hotplug %s\n" % name
            output += "iface %s %s %s\n" % (name, iface.get("mode", "inet"), iface.get("method", "manual"))
            options = iface.get("options", {})
            for opt_key in sorted(options):
                if options[opt_key] not in ("", None):
                    output += "  %s %s\n" % (opt_key, options[opt_key])
            output += "\n"
        return output

    def get_interfaces(self):
        """
        return the configuration using the following structure

        [
            {
              "name": "eth0",
              "auto": True,
              "method": "static",
              "options": {
                "address": "192.168.1.1",
                "netmask": "255.255.255.0",
                "gateway": "192.168.1.254",
                "dns-nameserver": "1.2.3.4"
              }
            }
        ]

        :return: list
        """
        res = []
        interfaces = {}
        prop = self.get()
        for iface_definition in prop:
            name = iface_definition[1]
            if name in interfaces:
                iface = interfaces[name]
            else:
                iface = dict(name=name)
                interfaces[name] = iface
                res.append(iface)
            # auto?
            if iface_definition[0] == "auto":
                iface["auto"] = True
            if iface_definition[0] == "allow-hotplug":
                iface["allow-hotplug"] = True
            elif iface_definition[0] == "iface":
                mode = iface_definition[2]
                iface["mode"] = mode
                method = iface_definition[3]
                iface["method"] = method
            # check for options
            if len(iface_definition) == 5:
                options = {}
                for opt in iface_definition[4]:
                    options[opt[0]] = opt[1]
                iface["options"] = options
        return res

    @staticmethod
    def probe():
        if exists(NETWORK_INTERFACES_CONFIG):
            return NetworkInterfacesAdapter(NETWORK_INTERFACES_CONFIG)
        return None

    def apply(self, interfaces):
        supported_methods = ["loopback", "dhcp", "static", "can", "manual", "ppp"]
        self.interfaces = filter(lambda i: i.get("method") in supported_methods, interfaces)
        with open(NETWORK_INTERFACES_CONFIG, "w", encoding="utf-8") as file:
            file.write(self.format())
        return []

    def read(self):
        return self.interfaces
