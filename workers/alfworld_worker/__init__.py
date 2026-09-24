"""Isolated ALFWorld worker package.

The worker intentionally contains no imports from the main HomeMaster
environment.  Communication is the versioned stdin/stdout NDJSON protocol.
"""
