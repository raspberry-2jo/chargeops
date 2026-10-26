"""Bounded OCPP handler outcome labels; no charger IDs or payloads in metrics."""
import asyncio
from functools import wraps
import time

from prometheus_client import Counter, Gauge, Histogram, REGISTRY


class OcppMetrics:
    def __init__(self, registry=REGISTRY):
        self.results = Counter('chargeops_ocpp_handler_results_total',
            'OCPP handler completions, not just received messages.', ['action', 'result'], registry=registry)
        self.duration = Histogram('chargeops_ocpp_request_seconds',
            'OCPP handler execution through result construction; excludes WebSocket send.', ['action', 'result'],
            buckets=(.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10), registry=registry)
        self.last_success = Gauge('chargeops_ocpp_last_success_timestamp_seconds',
            'Last successfully completed handler timestamp per action.', ['action'], registry=registry)
        for action in ('BootNotification', 'Heartbeat', 'StatusNotification', 'MeterValues'):
            for result in ('success', 'error', 'timeout', 'cancelled'):
                self.results.labels(action, result)

    def observe(self, action):
        def decorate(handler):
            @wraps(handler)
            async def wrapped(*args, **kwargs):
                started, result = time.monotonic(), 'success'
                try:
                    return await handler(*args, **kwargs)
                except (TimeoutError, asyncio.TimeoutError):
                    result = 'timeout'
                    raise
                except asyncio.CancelledError:
                    result = 'cancelled'
                    raise
                except Exception:
                    result = 'error'
                    raise
                finally:
                    self.results.labels(action, result).inc()
                    self.duration.labels(action, result).observe(time.monotonic() - started)
                    if result == 'success':
                        self.last_success.labels(action).set(time.time())
            return wrapped
        return decorate
