from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from temporalio import activity

from app.browser import PlaywrightApplicationBrowser
from app.db import SessionLocal
from app.integrations.gmail import GmailClient, GmailConfigurationError
from app.models import Application, ApplicationStatus, Artifact, Assessment, Escalation, Job, JobSource, JobStatus, OutreachMessage, SystemSetting
from app.qualification import QualificationService
from app.safety import PolicyBlockedError, enforce_application_policy, enforce_outreach_policy
from app.services import CandidateProfileService, PolicyService, SourceService


AUTOMATION_STATUS_KEY = "automation_status"


@activity.defn
async def run_discovery_cycle_activity() -> dict:
    """Poll every enabled public source and qualify only unseen jobs.

    This workflow never submits an application. Submission remains a separate,
    explicit policy-controlled workflow because no source page can establish a
    truthful answer to application questions.
    """

    started_at = datetime.now(timezone.utc)
    result: dict = {
        "state": "running",
        "started_at": started_at.isoformat(),
        "completed_at": None,
        "sources_polled": 0,
        "jobs_fetched": 0,
        "jobs_created": 0,
        "jobs_updated": 0,
        "jobs_assessed": 0,
        "jobs_qualified": 0,
        "errors": [],
    }
    with SessionLocal() as session:
        sources = session.scalars(select(JobSource).where(JobSource.enabled.is_(True))).all()
        if not sources:
            result["state"] = "waiting_for_sources"
        for source in sources:
            try:
                outcome = SourceService.poll(session, source)
                result["sources_polled"] += 1
                for key in ("fetched", "created", "updated"):
                    result[f"jobs_{key}"] += int(outcome.get(key, 0))
            except (httpx.HTTPError, ValueError) as exc:
                result["errors"].append({"source_id": source.id, "message": str(exc)[:500]})
        profile = CandidateProfileService.approved(session)
        if not profile:
            result["state"] = "waiting_for_profile"
            result["blocked_reason"] = "An approved factual candidate profile is required before qualification."
        elif sources:
            qualification = QualificationService()
            spec = qualification.default_spec(session)
            candidates = session.scalars(
                select(Job).where(Job.status == JobStatus.DISCOVERED).order_by(Job.first_seen_at.asc()).limit(100)
            ).all()
            for job in candidates:
                exists = session.scalar(
                    select(Assessment.id)
                    .where(Assessment.job_id == job.id, Assessment.agent_spec_id == spec.id)
                    .limit(1)
                )
                if exists:
                    continue
                assessment = qualification.assess(session, job, spec)
                result["jobs_assessed"] += 1
                if assessment.relevance_score >= float(spec.config.get("thresholds", {}).get("minimum_relevance", 0.65)):
                    result["jobs_qualified"] += 1
            result["state"] = "running"
        result["completed_at"] = datetime.now(timezone.utc).isoformat()
        setting = session.get(SystemSetting, AUTOMATION_STATUS_KEY)
        if setting:
            setting.value = result
        else:
            session.add(SystemSetting(key=AUTOMATION_STATUS_KEY, value=result))
        session.commit()
    return result


@activity.defn
async def poll_source_activity(source_id: str) -> dict:
    with SessionLocal() as session:
        source = session.get(JobSource, source_id)
        if not source or not source.enabled:
            return {"status": "skipped", "reason": "source not found or disabled"}
        return {"status": "completed", **SourceService.poll(session, source)}


@activity.defn
async def prepare_application_activity(application_id: str) -> dict:
    with SessionLocal() as session:
        application = session.get(Application, application_id)
        if not application:
            return {"status": "failed", "reason": "application not found"}
        profile = CandidateProfileService.approved(session)
        try:
            if not profile:
                raise PolicyBlockedError("No approved candidate profile exists.")
            enforce_application_policy(session, PolicyService.get(session))
        except PolicyBlockedError as exc:
            application.status = ApplicationStatus.BLOCKED
            session.commit()
            return {"status": "blocked", "reason": str(exc)}
        application.status = ApplicationStatus.READY
        session.commit()
        return {"status": "ready"}


@activity.defn
async def execute_application_browser_activity(application_id: str) -> dict:
    with SessionLocal() as session:
        application = session.get(Application, application_id)
        if not application:
            return {"status": "failed", "reason": "application not found"}
        job = session.get(Job, application.job_id)
        profile = CandidateProfileService.approved(session)
        if not job or not profile:
            return {"status": "failed", "reason": "job or approved candidate profile not found"}
        browser_facts = dict(profile.facts)
        if application.resume_artifact_id:
            resume = session.get(Artifact, application.resume_artifact_id)
            if resume and resume.uri.startswith("file://"):
                browser_facts["resume_path"] = resume.uri.removeprefix("file://")
        result = await PlaywrightApplicationBrowser().inspect_and_submit(job.url, browser_facts, application.id)
        application.browser_evidence_uri = result.evidence_uri
        if result.status == "submitted":
            application.status = ApplicationStatus.SUBMITTED
            application.submitted_at = datetime.now(timezone.utc)
            job.status = JobStatus.APPLIED
        elif result.status in ("needs_escalation", "unavailable"):
            application.status = ApplicationStatus.ESCALATED
            session.add(
                Escalation(
                    job_id=job.id,
                    workflow_id=f"application-{application.id}",
                    category="application_form",
                    question="; ".join(result.unknown_questions) if result.unknown_questions else (result.detail or "Browser worker is unavailable"),
                    context={"application_id": application.id, "url": job.url, "evidence_uri": result.evidence_uri},
                )
            )
        else:
            application.status = ApplicationStatus.FAILED
        session.commit()
        return {"status": result.status, "evidence_uri": result.evidence_uri, "detail": result.detail}


@activity.defn
async def send_outreach_activity(message_id: str) -> dict:
    with SessionLocal() as session:
        message = session.get(OutreachMessage, message_id)
        if not message:
            return {"status": "failed", "reason": "outreach message not found"}
        try:
            enforce_outreach_policy(session, PolicyService.get(session))
            provider_message_id, thread_id = GmailClient().send(message.recipient, message.subject, message.body)
            message.provider_message_id = provider_message_id
            message.status = "sent"
            message.sent_at = datetime.now(timezone.utc)
            session.commit()
            return {"status": "sent", "provider_message_id": provider_message_id, "thread_id": thread_id}
        except (PolicyBlockedError, GmailConfigurationError, httpx.HTTPError) as exc:
            message.status = "blocked" if isinstance(exc, PolicyBlockedError) else "failed"
            session.add(
                Escalation(
                    job_id=message.job_id,
                    workflow_id=f"outreach-{message.id}",
                    category="outreach",
                    question=str(exc),
                    context={"outreach_id": message.id, "recipient": message.recipient},
                )
            )
            session.commit()
            return {"status": message.status, "reason": str(exc)}
