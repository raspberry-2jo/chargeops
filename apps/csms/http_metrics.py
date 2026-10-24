"""HTTP response counters on the existing CSMS Prometheus registry.

Revision is the ReplicaSet hash in a normal csms-<hash>-<suffix> Pod name.
No request URLs, charger IDs, headers, bodies or tokens become metric labels.
"""
import os
import re

from prometheus_client import Counter, REGISTRY


def revision_from_hostname(hostname):
    match = re.fullmatch(r'csms-([a-z0-9]{5,20})-[a-z0-9]{5}', hostname)
    return match.group(1) if match else 'unknown'


class HTTPMetricsMiddleware:
    def __init__(self, app, registry=REGISTRY, hostname=None):
        self.app = app
        self.revision = revision_from_hostname(os.environ.get('HOSTNAME', '') if hostname is None else hostname)
        self.requests = Counter(
            'chargeops_csms_http_requests_total', 'Completed CSMS HTTP requests by response class',
            ['route', 'method', 'status_class', 'revision'], registry=registry)
        # A genuine zero error rate is different from absent instrumentation.
        for group in ['1xx', '2xx', '3xx', '4xx', '5xx']:
            self.requests.labels('/healthz', 'GET', group, self.revision).inc(0)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        status = 500
        method = scope.get('method', '')
        if method not in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS', 'CONNECT', 'TRACE'}:
            method = 'OTHER'

        async def record_response(message):
            nonlocal status
            if message['type'] == 'http.response.start':
                status = message['status']
            await send(message)

        try:
            await self.app(scope, receive, record_response)
        except Exception:
            status = 500
            raise
        finally:
            route = getattr(scope.get('route'), 'path', '__unmatched__')
            group = str(status // 100) + 'xx' if 100 <= status < 600 else '5xx'
            self.requests.labels(route, method, group, self.revision).inc()
