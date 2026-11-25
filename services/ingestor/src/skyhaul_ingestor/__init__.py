"""Skyhaul telemetry ingestor (#27, ADR 0010 and 0012): one thread that consumes
``ingestor.telemetry``, batch-writes Postgres, updates the Redis latest state and publishes
events."""
