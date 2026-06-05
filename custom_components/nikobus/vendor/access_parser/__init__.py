"""Vendored copy of claroty/access_parser (Apache-2.0).

A pure-Python MS Access (.mdb/.accdb) reader. Vendored because the
upstream PyPI sdist won't build on modern setuptools, and HA manifest
requirements must be pip-installable. Only ``construct`` is needed at
runtime (a clean wheel). The ``tabulate`` pretty-printer was stripped.

Upstream: https://github.com/claroty/access_parser  — see LICENSE.
"""

from .access_parser import AccessParser

__all__ = ["AccessParser"]
