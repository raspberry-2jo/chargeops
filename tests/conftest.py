"""Keep offline test fixtures independent of Jenkins deployment selection.

This runs before test modules import env_cd, whose defaults are read at import.
Individual configuration tests can still set explicit values with monkeypatch.
"""
import os

for name in ('DEV_GITOPS_OVERLAY', 'DEV_ARGO_APPLICATION'):
    os.environ.pop(name, None)
