"""HomeMaster CLI sub-package.

Backward compatibility: re-export key symbols so that
  from homemaster.cli import app
continues to work.
"""

from homemaster.cli.app import app

__all__ = ["app"]
