"""Shared token/artifact validators for public event projections.

Both the Web event projection and the Gateway public projection must agree on
what an artifact handle, an opaque id token, and a sha256 digest look like —
keep the patterns in one place so the two projections cannot drift.
"""

from __future__ import annotations

import re

ARTIFACT_HANDLE_RE = re.compile(r"^hm-artifact:[A-Za-z0-9_-]{32,128}$")
OPAQUE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,255}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
