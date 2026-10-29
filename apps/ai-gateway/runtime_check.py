"""Validate the stripped runtime without opening DB/LLM connections.

Run in the built image: python -m ai_gateway.runtime_check
This is not a replacement for API, dependency-service or vulnerability tests.
"""
import importlib
from importlib import metadata, util
import json

BUILD_TOOLS=('pip','setuptools','wheel')
RUNTIME_PACKAGES={
    'fastapi':'fastapi', 'pydantic':'pydantic', 'uvicorn':'uvicorn',
    'psycopg':'psycopg', 'psycopg_pool':'psycopg-pool', 'requests':'requests',
    'msgpack':'msgpack', 'urllib3':'urllib3',
}


def check(find_spec=util.find_spec, distribution=metadata.distribution,
          importer=importlib.import_module):
    for tool in BUILD_TOOLS:
        if find_spec(tool) is not None:
            raise RuntimeError('Build-tool module remains: '+tool)
        try:
            distribution(tool)
        except metadata.PackageNotFoundError:
            pass
        else:
            raise RuntimeError('Build-tool metadata remains: '+tool)
    versions={}
    for module,name in RUNTIME_PACKAGES.items():
        importer(module)
        versions[name]=distribution(name).version
    health=importer('ai_gateway.health')
    paths={route.path for route in health.app.routes}
    if not {'/healthz','/cicd-healthz'}.issubset(paths):
        raise RuntimeError('Expected AI Gateway health routes are missing')
    return {'build_tools_absent':list(BUILD_TOOLS),'runtime_imports':versions,
            'health_routes_present':True,'db_llm_verified':False}


if __name__=='__main__':
    print(json.dumps(check(),sort_keys=True))
