import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from hookitagain.api.auth import require_user_id
from hookitagain.db.models import Project
from hookitagain.db.session import get_session

api_router = APIRouter(dependencies=[Depends(require_user_id)])


def _rfc3339_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@api_router.get("/projects")
def list_projects( #protected endpoint - lists all projects for current user
    user_id: uuid.UUID = Depends(require_user_id),
    session: Session = Depends(get_session),
) -> dict:
    projects = session.scalars(select(Project).where(Project.user_id == user_id)).all()
    return {
        "items": [
            {
                "id": str(project.id),
                "name": project.name,
                "created_at": _rfc3339_utc(project.created_at),
            }
            for project in projects
        ]
    }
