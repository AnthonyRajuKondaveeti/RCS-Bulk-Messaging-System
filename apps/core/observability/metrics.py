"""
Prometheus Metrics

Provides application metrics for monitoring.

Metrics:
    - HTTP request counters and histograms
    - Campaign metrics (created, active, completed)
    - Message metrics (sent, delivered, failed)
    - Queue depth gauges
    - Worker processing times

Usage:
    from apps.core.observability.metrics import (
        campaign_created,
        message_sent,
        message_delivered,
    )
    
    campaign_created.inc()
    message_sent.inc()
"""

from prometheus_client import Counter, Histogram, Gauge, Info
import time


# Application info
app_info = Info('rcs_platform_app', 'RCS Platform application info')
app_info.info({
    'version': '1.0.0',
    'environment': 'production',
})

# HTTP Metrics
http_requests_total = Counter(
    'http_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status'],
)

http_request_duration_seconds = Histogram(
    'http_request_duration_seconds',
    'HTTP request duration in seconds',
    ['method', 'endpoint'],
)

# Campaign Metrics
campaigns_created_total = Counter(
    'campaigns_created_total',
    'Total campaigns created',
    ['tenant_id', 'campaign_type'],
)

campaigns_active = Gauge(
    'campaigns_active',
    'Number of currently active campaigns',
)

campaigns_completed_total = Counter(
    'campaigns_completed_total',
    'Total campaigns completed',
    ['tenant_id'],
)

# Message Metrics
messages_created_total = Counter(
    'messages_created_total',
    'Total messages created',
    ['campaign_id', 'channel'],
)

messages_sent_total = Counter(
    'messages_sent_total',
    'Total messages sent',
    ['channel', 'aggregator'],
)

messages_delivered_total = Counter(
    'messages_delivered_total',
    'Total messages delivered',
    ['channel'],
)

messages_failed_total = Counter(
    'messages_failed_total',
    'Total messages failed',
    ['channel', 'failure_reason'],
)

messages_fallback_total = Counter(
    'messages_fallback_total',
    'Total SMS fallbacks triggered',
    ['original_channel'],
)

# Queue Metrics
queue_depth = Gauge(
    'queue_depth',
    'Current queue depth',
    ['queue_name'],
)

queue_processing_duration_seconds = Histogram(
    'queue_processing_duration_seconds',
    'Queue job processing duration',
    ['queue_name', 'status'],
)

# Worker Metrics
worker_jobs_processed_total = Counter(
    'worker_jobs_processed_total',
    'Total jobs processed by workers',
    ['worker_name', 'status'],
)

worker_errors_total = Counter(
    'worker_errors_total',
    'Total worker errors',
    ['worker_name', 'error_type'],
)

# Database Metrics
db_query_duration_seconds = Histogram(
    'db_query_duration_seconds',
    'Database query duration',
    ['operation', 'table'],
)

db_connections_active = Gauge(
    'db_connections_active',
    'Active database connections',
)

# Aggregator Metrics
aggregator_requests_total = Counter(
    'aggregator_requests_total',
    'Total aggregator API requests',
    ['aggregator', 'operation', 'status'],
)

aggregator_request_duration_seconds = Histogram(
    'aggregator_request_duration_seconds',
    'Aggregator API request duration',
    ['aggregator', 'operation'],
)


class MetricsContext:
    """
    Context manager for timing operations
    
    Usage:
        with MetricsContext(http_request_duration_seconds, method='GET', endpoint='/campaigns'):
            # Your code here
            pass
    """
    
    def __init__(self, histogram, **labels):
        self.histogram = histogram
        self.labels = labels
        self.start_time = None
    
    def __enter__(self):
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = time.time() - self.start_time
        self.histogram.labels(**self.labels).observe(duration)


def record_http_request(method: str, endpoint: str, status: int, duration: float):
    """
    Record HTTP request metrics
    
    Args:
        method: HTTP method
        endpoint: Request endpoint
        status: Response status code
        duration: Request duration in seconds
    """
    http_requests_total.labels(
        method=method,
        endpoint=endpoint,
        status=status,
    ).inc()
    
    http_request_duration_seconds.labels(
        method=method,
        endpoint=endpoint,
    ).observe(duration)


def record_campaign_created(tenant_id: str, campaign_type: str):
    """Record campaign creation"""
    campaigns_created_total.labels(
        tenant_id=tenant_id,
        campaign_type=campaign_type,
    ).inc()


def record_message_sent(channel: str, aggregator: str):
    """Record message sent"""
    messages_sent_total.labels(
        channel=channel,
        aggregator=aggregator,
    ).inc()


def record_message_delivered(channel: str):
    """Record message delivered"""
    messages_delivered_total.labels(channel=channel).inc()


def record_message_failed(channel: str, failure_reason: str):
    """Record message failure"""
    messages_failed_total.labels(
        channel=channel,
        failure_reason=failure_reason,
    ).inc()


def update_queue_depth(queue_name: str, depth: int):
    """Update queue depth gauge"""
    queue_depth.labels(queue_name=queue_name).set(depth)


def record_worker_job(worker_name: str, status: str, duration: float):
    """
    Record worker job metrics
    
    Args:
        worker_name: Name of worker
        status: Job status (success, failed)
        duration: Processing duration
    """
    worker_jobs_processed_total.labels(
        worker_name=worker_name,
        status=status,
    ).inc()
    
    queue_processing_duration_seconds.labels(
        queue_name=worker_name,
        status=status,
    ).observe(duration)
