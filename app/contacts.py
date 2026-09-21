"""Contact discovery only records explicit public contact data in v1."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Contact, Job


EMAIL_PATTERN = re.compile(r"(?<![\w.+-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})", re.I)


class ContactDiscovery:
    def discover(self, session: Session, job: Job) -> list[Contact]:
        contacts: list[Contact] = []
        for email in sorted({value.lower() for value in EMAIL_PATTERN.findall(job.description)}):
            existing = session.scalar(select(Contact).where(Contact.job_id == job.id, Contact.email == email))
            if existing:
                contacts.append(existing)
                continue
            contact = Contact(job_id=job.id, email=email, source="public_job_description", confidence=0.9)
            session.add(contact)
            contacts.append(contact)
        session.commit()
        for contact in contacts:
            session.refresh(contact)
        return contacts
