import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from app.activities import execute_application_browser_activity, poll_source_activity, prepare_application_activity, run_discovery_cycle_activity, send_outreach_activity
from app.config import get_settings
from app.db import initialize_database
from app.temporal_client import ensure_continuous_discovery
from app.workflows import ApplicationWorkflow, ContinuousDiscoveryWorkflow, OutreachWorkflow, PollJobSourceWorkflow


async def run() -> None:
    settings = get_settings()
    initialize_database()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    await ensure_continuous_discovery(client)
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[PollJobSourceWorkflow, ContinuousDiscoveryWorkflow, ApplicationWorkflow, OutreachWorkflow],
        activities=[run_discovery_cycle_activity, poll_source_activity, prepare_application_activity, execute_application_browser_activity, send_outreach_activity],
    )
    await worker.run()


if __name__ == "__main__":
    logging.basicConfig(level=get_settings().log_level)
    asyncio.run(run())
