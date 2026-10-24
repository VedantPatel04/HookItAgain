import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from pydantic import BaseModel

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy import select

from hookitagain.api.auth import require_user_id
from hookitagain.api.errors import error_response
from hookitagain.db.models import Project
from hookitagain.db.session import get_session

api_router = APIRouter(dependencies=[Depends(require_user_id)])

class CreateProjectBody(BaseModel):
    name: str

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
    items = []
    for project in projects:
        items.append(
            {
                "id": str(project.id),
                "name": project.name,
                "created_at": _rfc3339_utc(project.created_at),
            }
        )
    return {"items": items}



# POST /api/projects
# 1. Add a body param - FastAPI 422s automatically if it is missing or not a string.
# 2. JWT is already checked: user_id comes from require_user_id.
# 3. Write a Project row sh.
# 4. IntegrityError on commit - rollback. Raise 404 : profile associated with user_id not_found.
# 5. Return
@api_router.post("/projects",status_code=201)
def create_project(
    body: CreateProjectBody,
    user_id: uuid.UUID = Depends(require_user_id),
    session: Session = Depends(get_session),
) -> dict:
    try:
        project=Project(user_id=user_id, name = body.name, created_at = datetime.now(timezone.utc))
        session.add(project)
        session.commit()
        session.refresh(project)
        
    except IntegrityError as exc:
        session.rollback() #rollback transaction, back to last successfull db state
        orig = getattr(exc, "orig", None) # specifically look for missing parent row(missing FK) error code 23503
        if getattr(orig, "pgcode", None) == "23503": 
            return error_response(404, "not_found", "No profile for this user.")
        raise #unexpected error (not a missing FK error becomes 500)
    return {"id": str(project.id), "name": project.name, "created_at": _rfc3339_utc(project.created_at)}


# GET /api/projects/{project_id}
# 1. Path param project_id (UUID). FastAPI 422s if it is not a UUID.
# 2. JWT is already checked: user_id comes from require_user_id.
# 3. Load the Project where id == project_id and user_id == this user.
# 4. No row: return 404 not_found (unknown id and other-user id are the same).
# 5. Return {id, name, created_at} via _rfc3339_utc. Default 200. Not {items: [...]}.
#
# Watch: do not use 403. Tenant-scope the query so another user's project looks missing.
@api_router.get("/projects/{project_id}")
def get_project(
    project_id: uuid.UUID,
    user_id: uuid.UUID = Depends(require_user_id),
    session: Session = Depends(get_session),
) -> dict:
    project_query = select(Project).where(Project.id == project_id, Project.user_id == user_id)
    projrun = session.scalars(project_query).one_or_none() #run the query on the db
    if projrun is None:
        return error_response(404, "not_found", "Project not found.")
    
    return {"id": str(projrun.id), "name": projrun.name, "created_at": _rfc3339_utc(projrun.created_at)}    