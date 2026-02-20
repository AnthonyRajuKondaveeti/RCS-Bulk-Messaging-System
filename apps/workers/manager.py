"""
Worker Manager

Manages all background workers as a single process.
Useful for development and small deployments.

For production, run each worker as separate processes/containers.

Workers:
    - Campaign Orchestrator: Executes campaigns
    - Message Dispatcher: Sends messages
    - Webhook Processor: Handles delivery updates
    - SMS Fallback: Handles SMS fallback

Usage:
    # Run all workers
    python -m apps.workers.manager
    
    # Run specific workers
    python -m apps.workers.manager --workers orchestrator,dispatcher
"""

import asyncio
import logging
import signal
import sys
from typing import List

from apps.workers.orchestrator.campaign_orchestrator import CampaignOrchestrator
from apps.workers.dispatcher.message_dispatcher import MessageDispatcher
from apps.workers.events.webhook_processor import WebhookProcessor
from apps.workers.fallback.sms_fallback_worker import SMSFallbackWorker


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class WorkerManager:
    """
    Manages multiple background workers
    
    Example:
        >>> manager = WorkerManager()
        >>> await manager.start_all()
    """
    
    def __init__(self):
        """Initialize worker manager"""
        self.workers = []
        self.running = False
    
    async def start_all(self) -> None:
        """Start all workers"""
        logger.info("🚀 Starting all workers...")
        
        try:
            # Create workers
            orchestrator = CampaignOrchestrator()
            dispatcher = MessageDispatcher(concurrency=10)
            webhook_processor = WebhookProcessor(concurrency=20)
            fallback_worker = SMSFallbackWorker(concurrency=5)
            
            # Start workers concurrently
            await asyncio.gather(
                orchestrator.start(),
                dispatcher.start(),
                webhook_processor.start(),
                fallback_worker.start(),
            )
            
            self.workers = [
                orchestrator,
                dispatcher,
                webhook_processor,
                fallback_worker,
            ]
            
            self.running = True
            
            logger.info("✅ All workers started successfully")
            logger.info("   - Campaign Orchestrator")
            logger.info("   - Message Dispatcher (10 workers)")
            logger.info("   - Webhook Processor (20 workers)")
            logger.info("   - SMS Fallback Worker (5 workers)")
            
        except Exception as e:
            logger.exception("Failed to start workers")
            raise
    
    async def stop_all(self) -> None:
        """Stop all workers"""
        logger.info("🛑 Stopping all workers...")
        
        self.running = False
        
        # Stop workers concurrently
        stop_tasks = []
        for worker in self.workers:
            stop_tasks.append(worker.stop())
        
        await asyncio.gather(*stop_tasks, return_exceptions=True)
        
        logger.info("✅ All workers stopped")


async def main():
    """Main entry point"""
    manager = WorkerManager()
    
    # Setup signal handlers
    def signal_handler(sig, frame):
        logger.info(f"Received signal {sig}")
        asyncio.create_task(manager.stop_all())
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Start all workers
        await manager.start_all()
        
        # Keep running
        while manager.running:
            await asyncio.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.exception("Worker manager error")
    finally:
        await manager.stop_all()


if __name__ == "__main__":
    asyncio.run(main())
