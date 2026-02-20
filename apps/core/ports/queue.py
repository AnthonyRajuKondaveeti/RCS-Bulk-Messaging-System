"""
Queue Port Interface

Defines the contract for message queue implementations.
Supports job queuing, priority handling, retry logic, and DLQ.

Implementations:
    - RabbitMQAdapter
    - BullMQAdapter
    - KafkaAdapter (future)
    
Pattern: Hexagonal Architecture (Ports & Adapters)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any, Callable, List
from datetime import datetime, timedelta
from enum import Enum


class QueuePriority(int, Enum):
    """Message priority levels"""
    LOW = 1
    MEDIUM = 5
    HIGH = 10
    URGENT = 20


@dataclass
class QueueMessage:
    """Message to be enqueued"""
    id: str  # Unique job ID
    queue_name: str
    payload: Dict[str, Any]
    priority: QueuePriority = QueuePriority.MEDIUM
    delay: Optional[timedelta] = None  # Delayed execution
    max_retries: int = 3
    retry_backoff: int = 60  # Seconds between retries
    metadata: Dict[str, Any] = None


@dataclass
class QueueJob:
    """Job retrieved from queue"""
    id: str
    queue_name: str
    payload: Dict[str, Any]
    attempt: int
    max_retries: int
    enqueued_at: datetime
    metadata: Dict[str, Any] = None


@dataclass
class QueueStats:
    """Queue statistics"""
    queue_name: str
    pending: int
    active: int
    completed: int
    failed: int
    delayed: int
    total: int


class QueuePort(ABC):
    """
    Abstract interface for message queues
    
    Responsibilities:
        - Job enqueueing with priority
        - Delayed job scheduling
        - Retry mechanism with exponential backoff
        - Dead Letter Queue (DLQ) handling
        - Job acknowledgment
    
    Guarantees:
        - At-least-once delivery
        - Priority ordering (best effort)
        - Job persistence
    """
    
    @abstractmethod
    async def enqueue(
        self,
        message: QueueMessage,
    ) -> str:
        """
        Add a job to the queue
        
        Args:
            message: Job to enqueue
            
        Returns:
            Job ID for tracking
            
        Example:
            >>> await queue.enqueue(QueueMessage(
            ...     id=str(message_id),
            ...     queue_name="message.send",
            ...     payload={"message_id": str(message_id)},
            ...     priority=QueuePriority.HIGH,
            ... ))
        """
        pass
    
    @abstractmethod
    async def enqueue_batch(
        self,
        messages: List[QueueMessage],
    ) -> List[str]:
        """
        Add multiple jobs to queue atomically
        
        Args:
            messages: List of jobs to enqueue
            
        Returns:
            List of job IDs
        """
        pass
    
    @abstractmethod
    async def dequeue(
        self,
        queue_name: str,
        timeout: Optional[int] = None,
    ) -> Optional[QueueJob]:
        """
        Retrieve a job from queue
        
        Args:
            queue_name: Name of queue to consume from
            timeout: Block for N seconds waiting for job (None = no block)
            
        Returns:
            Job to process or None if timeout/empty
            
        Note:
            Job must be acknowledged or will be requeued
        """
        pass
    
    @abstractmethod
    async def acknowledge(
        self,
        job_id: str,
    ) -> None:
        """
        Mark job as successfully completed
        
        Args:
            job_id: ID of completed job
        """
        pass
    
    @abstractmethod
    async def reject(
        self,
        job_id: str,
        requeue: bool = False,
        reason: Optional[str] = None,
    ) -> None:
        """
        Mark job as failed
        
        Args:
            job_id: ID of failed job
            requeue: Whether to retry (respects max_retries)
            reason: Failure reason for logging
        """
        pass
    
    @abstractmethod
    async def schedule(
        self,
        message: QueueMessage,
        scheduled_for: datetime,
    ) -> str:
        """
        Schedule a job for future execution
        
        Args:
            message: Job to schedule
            scheduled_for: When to execute
            
        Returns:
            Job ID
        """
        pass
    
    @abstractmethod
    async def get_job_status(
        self,
        job_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get current status of a job
        
        Args:
            job_id: Job identifier
            
        Returns:
            Job status dict with state, attempts, etc.
        """
        pass
    
    @abstractmethod
    async def get_queue_stats(
        self,
        queue_name: str,
    ) -> QueueStats:
        """
        Get statistics for a queue
        
        Args:
            queue_name: Queue to inspect
            
        Returns:
            Current queue statistics
        """
        pass
    
    @abstractmethod
    async def purge_queue(
        self,
        queue_name: str,
    ) -> int:
        """
        Remove all jobs from queue (DANGEROUS)
        
        Args:
            queue_name: Queue to purge
            
        Returns:
            Number of jobs removed
        """
        pass
    
    @abstractmethod
    async def move_to_dlq(
        self,
        job_id: str,
        reason: str,
    ) -> None:
        """
        Move failed job to Dead Letter Queue
        
        Args:
            job_id: Job that permanently failed
            reason: Why it was moved to DLQ
        """
        pass
    
    @abstractmethod
    async def get_dlq_jobs(
        self,
        queue_name: str,
        limit: int = 100,
    ) -> List[QueueJob]:
        """
        Retrieve jobs from Dead Letter Queue
        
        Args:
            queue_name: Original queue name
            limit: Max jobs to retrieve
            
        Returns:
            List of failed jobs
        """
        pass
    
    @abstractmethod
    async def retry_dlq_job(
        self,
        job_id: str,
    ) -> None:
        """
        Retry a job from DLQ
        
        Args:
            job_id: Job to retry
        """
        pass
    
    @abstractmethod
    async def subscribe(
        self,
        queue_name: str,
        handler: Callable[[QueueJob], None],
        prefetch: int = 10,
    ) -> None:
        """
        Subscribe to queue with a handler function
        
        Args:
            queue_name: Queue to subscribe to
            handler: Async function to process jobs
            prefetch: Number of jobs to prefetch
            
        Example:
            >>> async def process_message(job: QueueJob):
            ...     message_id = job.payload["message_id"]
            ...     await send_message(message_id)
            >>> 
            >>> await queue.subscribe("message.send", process_message)
        """
        pass
    
    @abstractmethod
    async def close(self) -> None:
        """Close queue connection gracefully"""
        pass


class QueueException(Exception):
    """Base exception for queue errors"""
    pass


class QueueConnectionException(QueueException):
    """Raised when queue connection fails"""
    pass


class JobNotFoundException(QueueException):
    """Raised when job is not found"""
    pass
