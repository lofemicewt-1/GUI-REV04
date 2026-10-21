"""Test package setup."""

from .tk_cleanup import install_unittest_tk_gc_safety
from ._diag_tk_roots import install_tk_root_tracking

install_tk_root_tracking()
install_unittest_tk_gc_safety()
