"""Legacy shim, for hosts whose pip or setuptools predates PEP 660.

Everything is declared in `pyproject.toml`; this file adds nothing to it. It
exists because `pip install -e .` on an older toolchain refuses outright —
"it cannot be installed in editable mode ... does not have a setup.py" — and on
a server that toolchain is whatever the distribution shipped. With this present,
pip falls back to the legacy editable path instead of stopping.

Nothing needs installing to use the tool: `python3 -m wiki_generator` runs from
a checkout, which is what the container image does.
"""

from setuptools import setup

setup()
