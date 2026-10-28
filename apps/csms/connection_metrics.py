"""Classify accepted-session closes and confirmed same-process reconnects."""
from collections import OrderedDict
import math
import time
from prometheus_client import Counter, Gauge, Histogram, REGISTRY


class ConnectionMetrics:
    def __init__(self, registry=REGISTRY, clock=time.monotonic, capacity=10000, recovery_timeout=60):
        if not math.isfinite(recovery_timeout) or recovery_timeout <= 0 or capacity < 1:
            raise ValueError('Positive recovery timeout and capacity required')
        self.clock, self.capacity, self.seen = clock, capacity, OrderedDict()
        self.recovery_timeout = recovery_timeout
        self.pending, self.active = OrderedDict(), {}
        self.disconnect = Counter('chargeops_csms_disconnect_total', 'Accepted WebSocket sessions closed',
                                  ['kind', 'reason'], registry=registry)
        self.reconnect = Counter('chargeops_csms_reconnect_total',
            'Confirmed repeat accepted ID within one hour on this process; excludes cross-Pod reconnects',
            ['kind', 'scope'], registry=registry)
        self.recovery = Counter('chargeops_csms_recovery_outcomes_total',
            'Same-process closed-session cohorts resolved by accepted reconnect or timeout; not client attempts',
            ['kind', 'scope', 'result'], registry=registry)
        self.recovery_seconds = Histogram('chargeops_csms_recovery_seconds',
            'Close detection to accepted reconnect on same process; successful recoveries within timeout only',
            ['kind', 'scope'], buckets=(.1,.25,.5,1,2,4,8,15,30,60,120), registry=registry)
        self.pending_gauge = Gauge('chargeops_csms_recovery_pending',
            'Unresolved same-process closed sessions; lost on process termination', registry=registry)
        self.discarded = Counter('chargeops_csms_recovery_discarded_total',
            'Observation cohorts evicted by capacity; never counted as failed reconnects', registry=registry)
        self.timeout_gauge = Gauge('chargeops_csms_recovery_timeout_seconds',
            'Observation deadline for same-process recovery cohorts', registry=registry)
        self.timeout_gauge.set(recovery_timeout)
        self.pending_gauge.set(0)
        for result in ('success', 'timeout'):
            self.recovery.labels('service', 'pod_local', result)
        self.recovery_seconds.labels('service', 'pod_local')
        for kind in ('service', 'fixture'):
            self.reconnect.labels(kind, 'pod_local')
            for reason in ('normal', 'server_shutdown', 'abnormal', 'handler_error'):
                self.disconnect.labels(kind, reason)

    def opened(self, cid, fixture=False, session=None):
        now = self.clock()
        self.expire()
        closed_at = self.pending.pop(cid, None)
        if closed_at is not None and not fixture:
            self.recovery.labels('service', 'pod_local', 'success').inc()
            self.recovery_seconds.labels('service', 'pod_local').observe(now - closed_at)
        self.pending_gauge.set(len(self.pending))
        self.active[cid] = session
        previous = self.seen.pop(cid, None)
        if previous is not None and now - previous <= 3600:
            self.reconnect.labels('fixture' if fixture else 'service', 'pod_local').inc()
        self.seen[cid] = now
        while len(self.seen) > self.capacity:
            self.seen.popitem(last=False)

    def expire(self):
        now = self.clock()
        while self.pending:
            cid, closed_at = next(iter(self.pending.items()))
            if now - closed_at <= self.recovery_timeout:
                break
            self.pending.pop(cid)
            self.recovery.labels('service', 'pod_local', 'timeout').inc()
        self.pending_gauge.set(len(self.pending))

    def closed(self, close_code, draining=False, fixture=False, handler_error=False, cid=None, session=None):
        reason = ('server_shutdown' if draining else 'normal' if close_code in (1000, 1001)
                  else 'handler_error' if handler_error else 'abnormal')
        self.disconnect.labels('fixture' if fixture else 'service', reason).inc()
        self.expire()
        if cid is not None and cid in self.active and self.active[cid] == session:
            self.active.pop(cid)
            if not fixture and not draining:
                self.pending[cid] = self.clock()
                while len(self.pending) > self.capacity:
                    self.pending.popitem(last=False)
                    self.discarded.inc()
                self.pending_gauge.set(len(self.pending))
        return reason
