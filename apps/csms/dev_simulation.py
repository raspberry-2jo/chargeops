"""Explicit Dev-only, bounded virtual charger identities (no charger DB writes)."""
import re


def simulation_ids(environ):
    if environ.get('DEV_SIMULATION_ENABLED', 'false') != 'true':
        return frozenset()
    if environ.get('ENVIRONMENT') != 'dev' or environ.get('POD_NAMESPACE') != 'dev':
        raise ValueError('Dev simulation requires ENVIRONMENT=dev and namespace dev')
    ids = environ.get('DEV_SIMULATION_IDS', '').split(',')
    if not 1 <= len(ids) <= 10 or any(not re.fullmatch(r'CI-DEV-[A-Za-z0-9_-]{1,64}', cid) for cid in ids):
        raise ValueError('Provide 1-10 explicit CI-DEV simulation IDs')
    return frozenset(ids)
