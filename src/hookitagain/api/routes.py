import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from pydantic import BaseModel

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy import select

from hookitagain.api.auth import require_user_id
from hookitagain.api.errors import error_response
from hookitagain.api.ssrf import https_destination_url_rejected
from hookitagain.crypto import encrypt_secret
from hookitagain.db.models import Destination, Project, Source, SourceSecret
from hookitagain.db.session import get_session
from hookitagain.settings import get_settings

api_router = APIRouter(dependencies=[Depends(require_user_id)])

class CreateProjectBody(BaseModel):
    name: str


class CreateDestinationBody(BaseModel): # schema for request body
    name: str
    type: str
    url: str | None = None


class CreateSourceBody(BaseModel): # schema for request body
    name: str                   
    provider: str                # "source" is an identity assigned to a provider used by Ingest functionality   
    destination_id: uuid.UUID


def _rfc3339_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# Load a Project for this user.
# 1. id == project_id and user_id == this user.
# 2. No row: None (unknown id and other-user id are the same).
def _project_for_user(
    session: Session, project_id: uuid.UUID, user_id: uuid.UUID
) -> Project | None:
    query = select(Project).where(Project.id == project_id, Project.user_id == user_id)
    return session.scalars(query).one_or_none() #run the query on the db


# Load a Destination for this user.
# 1. Join destinations to projects.
# 2. Destination.id == destination_id and Project.user_id == this user.
# 3. No row: None (unknown id and other-user id are the same).
def _destination_for_user(
    session: Session, destination_id: uuid.UUID, user_id: uuid.UUID
) -> Destination | None:
    query = (
        select(Destination)
        .join(Project, Destination.project_id == Project.id)
        .where(Destination.id == destination_id, Project.user_id == user_id)
    )
    return session.scalars(query).one_or_none() #run the query on the db


# Load a Source for this user.
# 1. Join sources to projects.
# 2. Source.id == source_id and Project.user_id == this user.
# 3. No row: None (unknown id and other-user id are the same).
def _source_for_user(
    session: Session, source_id: uuid.UUID, user_id: uuid.UUID
) -> Source | None:
    query = (
        select(Source)
        .join(Project, Source.project_id == Project.id)
        .where(Source.id == source_id, Project.user_id == user_id)
    )
    return session.scalars(query).one_or_none() #run the query on the db


# ingest_url for GET/list (and create).
# {INGEST_BASE_URL}/ingest/{source_id}. strip a trailing slash on the base so the path is not doubled.
def _ingest_url(source_id: uuid.UUID) -> str:
    base = str(get_settings().ingest_base_url).rstrip("/")
    return f"{base}/ingest/{source_id}"


# HMAC shared secret for a source or destination.
# token_urlsafe(32). this is not the Fernet key (SECRET_ENCRYPTION_KEY wraps ciphertext at rest).
def _new_shared_secret() -> str:
    return secrets.token_urlsafe(32)


# Public destination fields. no signing_secret (plaintext only on create/rotate).
def _destination_public(destination: Destination) -> dict:
    return {
        "id": str(destination.id),
        "project_id": str(destination.project_id),
        "name": destination.name,
        "type": destination.type,
        "url": destination.url,
        "created_at": _rfc3339_utc(destination.created_at),
    }


# Public source fields plus ingest_url. no secret (plaintext only on create/rotate).
def _source_public(source: Source) -> dict:
    return {
        "id": str(source.id),
        "project_id": str(source.project_id),
        "name": source.name,
        "provider": source.provider,
        "destination_id": str(source.destination_id),
        "ingest_url": _ingest_url(source.id),
        "created_at": _rfc3339_utc(source.created_at),
    }


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


# POST /api/projects/{project_id}/destinations
# 1. Path param project_id (UUID). FastAPI 422s if it is not a UUID.
# 2. JWT is already checked: user_id comes from require_user_id.
# 3. Load the Project where id == project_id and user_id == this user. No row: 404 not_found.
# 4. type cli is 422 this increment. anything other than https is 422.
# 5. url required for https. SSRF at write (scheme https, blocked literal IP, blocked resolved addresses).
# 6. New destination signing secret (token_urlsafe(32)). Fernet wrap into signing_secret_ciphertext.
#    url stays the original https string.
# 7. Return 201 {id, project_id, name, type, url, signing_secret, created_at}. signing_secret only here and on rotate.
@api_router.post("/projects/{project_id}/destinations", status_code=201)
def create_destination(
    project_id: uuid.UUID,
    body: CreateDestinationBody,
    user_id: uuid.UUID = Depends(require_user_id),
    session: Session = Depends(get_session),
) -> dict:
    project = _project_for_user(session, project_id, user_id)
    if project is None:
        return error_response(404, "not_found", "Project not found.")

    if body.type == "cli":
        return error_response(422, "validation_error", "CLI destinations are not enabled yet.")
    if body.type != "https":
        return error_response(422, "validation_error", "Destination type must be https.")
    if body.url is None:
        return error_response(422, "validation_error", "HTTPS destinations require a url.")

    ssrf_message = https_destination_url_rejected(body.url) # scheme, literal IP, then every resolved address
    if ssrf_message is not None:
        return error_response(422, "validation_error", ssrf_message)

    signing_secret = _new_shared_secret() # plaintext destination signing secret, once
    destination = Destination(
        project_id=project.id,
        name=body.name,
        type="https",
        url=body.url, # original https string. not ciphertext
        signing_secret_ciphertext=encrypt_secret(signing_secret), # Fernet BYTEA
        created_at=datetime.now(timezone.utc),
    )
    session.add(destination)
    session.commit()
    session.refresh(destination)

    item = _destination_public(destination)
    item["signing_secret"] = signing_secret # omitted from later GET and list
    return item


# GET /api/projects/{project_id}/destinations
# 1. Path param project_id (UUID). FastAPI 422s if it is not a UUID.
# 2. JWT is already checked: user_id comes from require_user_id.
# 3. Load the Project where id == project_id and user_id == this user. No row: 404 not_found.
# 4. List destinations in that project. no signing_secret.
@api_router.get("/projects/{project_id}/destinations")
def list_destinations(
    project_id: uuid.UUID,
    user_id: uuid.UUID = Depends(require_user_id),
    session: Session = Depends(get_session),
) -> dict:
    project = _project_for_user(session, project_id, user_id)
    if project is None:
        return error_response(404, "not_found", "Project not found.") # missing project is 404, not {items: []}

    destination_query = select(Destination).where(Destination.project_id == project.id)
    destinations = session.scalars(destination_query).all() #run the query on the db
    items = []
    for destination in destinations:
        items.append(_destination_public(destination)) # no signing_secret
    return {"items": items}


# GET /api/destinations/{destination_id}
# 1. Path param destination_id (UUID). FastAPI 422s if it is not a UUID.
# 2. JWT is already checked: user_id comes from require_user_id.
# 3. Join destinations to projects. tenant-scope on Project.user_id.
# 4. No row: 404 not_found (unknown id and other-user id are the same).
# 5. Return public fields. no signing_secret.
@api_router.get("/destinations/{destination_id}")
def get_destination(
    destination_id: uuid.UUID,
    user_id: uuid.UUID = Depends(require_user_id),
    session: Session = Depends(get_session),
) -> dict:
    destination = _destination_for_user(session, destination_id, user_id) # join + tenant-scope
    if destination is None:
        return error_response(404, "not_found", "Destination not found.")
    return _destination_public(destination) # no signing_secret


# POST /api/destinations/{destination_id}/rotate-signing-secret
# 1. Path param destination_id (UUID). FastAPI 422s if it is not a UUID.
# 2. JWT is already checked: user_id comes from require_user_id.
# 3. Join destinations to projects. tenant-scope on Project.user_id. No row: 404 not_found.
# 4. New destination signing secret (token_urlsafe(32)). replace signing_secret_ciphertext.
# 5. Return 200 {signing_secret}. old secret no longer decrypts to the old string.
@api_router.post("/destinations/{destination_id}/rotate-signing-secret")
def rotate_destination_signing_secret(
    destination_id: uuid.UUID,
    user_id: uuid.UUID = Depends(require_user_id),
    session: Session = Depends(get_session),
) -> dict:
    destination = _destination_for_user(session, destination_id, user_id) # join + tenant-scope
    if destination is None:
        return error_response(404, "not_found", "Destination not found.")

    signing_secret = _new_shared_secret() # plaintext once. old ciphertext is replaced
    destination.signing_secret_ciphertext = encrypt_secret(signing_secret)
    session.commit()
    return {"signing_secret": signing_secret}


# POST /api/projects/{project_id}/sources
# 1. Path param project_id (UUID). FastAPI 422s if it is not a UUID.
# 2. JWT is already checked: user_id comes from require_user_id.
# 3. Load the Project where id == project_id and user_id == this user. No row: 404 not_found.
# 4. Destination must exist in that project. else 404 not_found.
# 5. provider generic_hmac or github. else 422.
# 6. New source secret (token_urlsafe(32)). Fernet wrap into source_secrets.ciphertext.
# 7. Return 201 with secret once and ingest_url. later GET/list omit secret.
@api_router.post("/projects/{project_id}/sources", status_code=201)
def create_source(
    project_id: uuid.UUID,
    body: CreateSourceBody,
    user_id: uuid.UUID = Depends(require_user_id),
    session: Session = Depends(get_session),
) -> dict:
    project = _project_for_user(session, project_id, user_id)
    if project is None:
        return error_response(404, "not_found", "Project not found.")

    if body.provider not in ("generic_hmac", "github"):
        return error_response(422, "validation_error", "Provider must be generic_hmac or github.")

    destination_query = select(Destination).where(
        Destination.id == body.destination_id,
        Destination.project_id == project.id, # this project, not another of the user's
    )
    destination = session.scalars(destination_query).one_or_none()
    if destination is None:
        return error_response(404, "not_found", "Destination not found.")

    secret = _new_shared_secret() # plaintext source secret, once
    now = datetime.now(timezone.utc)
    source = Source(
        project_id=project.id,
        name=body.name,
        provider=body.provider,
        destination_id=destination.id,
        created_at=now,
    )
    session.add(source)
    session.flush() # source.id from gen_random_uuid() before source_secrets insert

    source_secret = SourceSecret(
        source_id=source.id,
        project_id=project.id,
        ciphertext=encrypt_secret(secret), # Fernet BYTEA
        created_at=now,
    )
    session.add(source_secret)
    session.commit()
    session.refresh(source)

    item = _source_public(source)
    item["secret"] = secret # omitted from later GET and list
    return item


# GET /api/projects/{project_id}/sources
# 1. Path param project_id (UUID). FastAPI 422s if it is not a UUID.
# 2. JWT is already checked: user_id comes from require_user_id.
# 3. Load the Project where id == project_id and user_id == this user. No row: 404 not_found.
# 4. List sources in that project. public fields plus ingest_url. no secret.
@api_router.get("/projects/{project_id}/sources")
def list_sources(
    project_id: uuid.UUID,
    user_id: uuid.UUID = Depends(require_user_id),
    session: Session = Depends(get_session),
) -> dict:
    project = _project_for_user(session, project_id, user_id)
    if project is None:
        return error_response(404, "not_found", "Project not found.") # missing project is 404, not {items: []}

    source_query = select(Source).where(Source.project_id == project.id)
    sources = session.scalars(source_query).all() #run the query on the db
    items = []
    for source in sources:
        items.append(_source_public(source)) # ingest_url, no secret
    return {"items": items}


# GET /api/sources/{source_id}
# 1. Path param source_id (UUID). FastAPI 422s if it is not a UUID.
# 2. JWT is already checked: user_id comes from require_user_id.
# 3. Join sources to projects. tenant-scope on Project.user_id.
# 4. No row: 404 not_found (unknown id and other-user id are the same).
# 5. Return public fields plus ingest_url. no secret.
@api_router.get("/sources/{source_id}")
def get_source(
    source_id: uuid.UUID,
    user_id: uuid.UUID = Depends(require_user_id),
    session: Session = Depends(get_session),
) -> dict:
    source = _source_for_user(session, source_id, user_id) # join + tenant-scope
    if source is None:
        return error_response(404, "not_found", "Source not found.")
    return _source_public(source) # ingest_url, no secret


# POST /api/sources/{source_id}/rotate-secret
# 1. Path param source_id (UUID). FastAPI 422s if it is not a UUID.
# 2. JWT is already checked: user_id comes from require_user_id.
# 3. Join sources to projects. tenant-scope on Project.user_id. No row: 404 not_found.
# 4. Load source_secrets for that source. No row: 404 not_found.
# 5. New source secret (token_urlsafe(32)). replace ciphertext.
# 6. Return 200 {secret}. old secret no longer decrypts to the old string.
@api_router.post("/sources/{source_id}/rotate-secret")
def rotate_source_secret(
    source_id: uuid.UUID,
    user_id: uuid.UUID = Depends(require_user_id),
    session: Session = Depends(get_session),
) -> dict:
    source = _source_for_user(session, source_id, user_id) # join + tenant-scope
    if source is None:
        return error_response(404, "not_found", "Source not found.")

    secret_row = session.get(SourceSecret, source.id) # ciphertext lives on source_secrets, not sources
    if secret_row is None:
        return error_response(404, "not_found", "Source secret not found.")

    secret = _new_shared_secret() # plaintext once. old ciphertext is replaced
    secret_row.ciphertext = encrypt_secret(secret)
    session.commit()
    return {"secret": secret}
 