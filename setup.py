#!/usr/bin/env python3

import os

import setuptools


def get_version():
    return os.environ.get("DEB_VERSION", "0.0.0").split("~")[0].replace("-", "+")


setuptools.setup(
    name="wb-nm-helper",
    version=get_version(),
    description="wb-mqtt-confed backend for network configuration",
    license="MIT",
    author="Petr Krasnoshchekov",
    author_email="petr.krasnoshchekov@wirenboard.ru",
    maintainer="Wiren Board Team",
    maintainer_email="info@wirenboard.com",
    url="https://github.com/wirenboard/wb-nm-helper",
    packages=["wb.nm_helper"],
    scripts=["bin/wb-nm-helper", "bin/wb-mqtt-nm-helper"],
)
