#!/usr/bin/env python3

from setuptools import setup


def get_version():
    with open("debian/changelog", "r", encoding="utf-8") as f:
        return f.readline().split()[1][1:-1]


setup(
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
)
