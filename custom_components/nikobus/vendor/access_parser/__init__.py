"""Vendored copy of claroty/access_parser (Apache-2.0).

A pure-Python MS Access (.mdb/.accdb) reader. Vendored because the
upstream PyPI sdist won't build on modern setuptools, and HA manifest
requirements must be pip-installable. Only ``construct`` is needed at
runtime (a clean wheel). The ``tabulate`` pretty-printer was stripped,
and the optional-``MSysObjects`` chunk-parse failures were downgraded
from ERROR to DEBUG (that metadata is unused and the parser falls back
cleanly — at ERROR it floods the HA log).

Upstream: https://github.com/claroty/access_parser  — see LICENSE.
"""

from .access_parser import AccessParser

__all__ = ["AccessParser"]
