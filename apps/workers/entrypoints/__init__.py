# Worker entrypoints package
# Each module is a standalone process entry point for one worker type.
#
# Usage:
#   python -m apps.workers.entrypoints.dispatcher
#   python -m apps.workers.entrypoints.orchestrator
#   python -m apps.workers.entrypoints.webhook
#   python -m apps.workers.entrypoints.fallback
#
# Or via Docker compose service command.
