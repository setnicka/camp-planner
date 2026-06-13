"""Single source of the running app version.

Read from the installed package metadata (the `version` in pyproject.toml at install
time), so there's no second copy to keep in sync. In a deployed image the version
reflects whatever was `pip install`ed; running from an uninstalled source tree falls
back to a dev marker.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("camp-planner")
except PackageNotFoundError:  # source tree without an install
    __version__ = "0.0.0+dev"
