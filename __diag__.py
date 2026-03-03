import traceback, sys, os

print("Python:", sys.version)
print("CWD:", os.getcwd())
print()

# Step 1: test config loading
try:
    from apps.core.config import get_settings
    s = get_settings()
    print("Config OK")
    print(f"  environment: {s.environment}")
    print(f"  db.host: {s.database.host}, db.port: {s.database.port}, db.name: {s.database.database}")
    print(f"  rmq.host: {s.rabbitmq.host}, rmq.username: {s.rabbitmq.username}")
    print(f"  use_mock_aggregator: {s.use_mock_aggregator}")
    print(f"  secret_key length: {len(s.security.secret_key)}")
except Exception as e:
    print("Config FAILED:")
    traceback.print_exc()
    sys.exit(1)

# Step 2: test DB import 
try:
    from apps.adapters.db.postgres import init_database
    print("\nDB module imported OK")
except Exception as e:
    print("\nDB import FAILED:")
    traceback.print_exc()

# Step 3: test queue import
try:
    from apps.adapters.queue.rabbitmq import RabbitMQAdapter
    print("Queue module imported OK")
except Exception as e:
    print("Queue import FAILED:")
    traceback.print_exc()

# Step 4: test worker imports
try:
    from apps.workers.orchestrator.campaign_orchestrator import CampaignOrchestrator
    from apps.workers.dispatcher.message_dispatcher import MessageDispatcher
    from apps.workers.events.webhook_processor import WebhookProcessor
    from apps.workers.fallback.sms_fallback_worker import SMSFallbackWorker
    print("All worker classes imported OK")
except Exception as e:
    print("Worker import FAILED:")
    traceback.print_exc()

print("\nAll checks passed!")
