"""Classify accepted-session closes and confirmed same-process reconnects."""
from collections import OrderedDict
import time
from prometheus_client import Counter, REGISTRY


class ConnectionMetrics:
    def __init__(self, registry=REGISTRY, clock=time.monotonic, capacity=10000):
        self.clock, self.capacity, self.seen = clock, capacity, OrderedDict()
        self.disconnect = Counter('chargeops_csms_disconnect_total', 'Accepted WebSocket sessions closed',
                                  ['kind', 'reason'], registry=registry)
        self.reconnect = Counter('chargeops_csms_reconnect_total',
            'Confirmed repeat accepted ID within one hour on this process; excludes cross-Pod reconnects',
            ['kind', 'scope'], registry=registry)
        for kind in ('service', 'fixture'):
            self.reconnect.labels(kind, 'pod_local')
            for reason in ('normal', 'server_shutdown', 'abnormal', 'handler_error'):
                self.disconnect.labels(kind, reason)

    def opened(self, cid, fixture=False):
        now = self.clock()
        previous = self.seen.pop(cid, None)
        if previous is not None and now - previous <= 3600:
            self.reconnect.labels('fixture' if fixture else 'service', 'pod_local').inc()
        self.seen[cid] = now
        while len(self.seen) > self.capacity:
            self.seen.popitem(last=False)

    def closed(self, close_code, draining=False, fixture=False, handler_error=False):
        reason = ('server_shutdown' if draining else 'normal' if close_code in (1000, 1001)
                  else 'handler_error' if handler_error else 'abnormal')
        self.disconnect.labels('fixture' if fixture else 'service', reason).inc()
        return reason
