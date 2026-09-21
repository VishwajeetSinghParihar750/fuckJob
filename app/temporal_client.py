from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

from app.config import get_settings
from app.workflows import ApplicationWorkflow, ContinuousDiscoveryWorkflow, OutreachWorkflow, PollJobSourceWorkflow


async def ensure_continuous_discovery(client: Client | None = None) -> bool:
    settings = get_settings()
    if not settings.automation_enabled:
        return False
    temporal = client or await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    try:
        await temporal.start_workflow(
            ContinuousDiscoveryWorkflow.run,
            settings.automation_poll_interval_minutes,
            id="continuous-discovery",
            task_queue=settings.temporal_task_queue,
        )
        return True
    except WorkflowAlreadyStartedError:
        return False


async def start_application(application_id: str) -> str:
    settings = get_settings()
    workflow_id = f"application-{application_id}"
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    await client.start_workflow(ApplicationWorkflow.run, application_id, id=workflow_id, task_queue=settings.temporal_task_queue)
    return workflow_id


async def start_source_poll(source_id: str) -> str:
    settings = get_settings()
    workflow_id = f"source-poll-{source_id}"
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    await client.start_workflow(PollJobSourceWorkflow.run, source_id, id=workflow_id, task_queue=settings.temporal_task_queue)
    return workflow_id


async def start_outreach(message_id: str) -> str:
    settings = get_settings()
    workflow_id = f"outreach-{message_id}"
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    await client.start_workflow(OutreachWorkflow.run, message_id, id=workflow_id, task_queue=settings.temporal_task_queue)
    return workflow_id


async def resolve_application(application_id: str, resolution: str) -> None:
    settings = get_settings()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    handle = client.get_workflow_handle(f"application-{application_id}")
    await handle.signal(ApplicationWorkflow.resolve, resolution)
