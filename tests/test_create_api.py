import os
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import jwt
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from hookitagain.api.main import app
from hookitagain.crypto import decrypt_secret
from hookitagain.db.models import Destination, Profile, SourceSecret
from hookitagain.db.session import get_engine
from hookitagain.settings import get_settings

# HELPER FUNCTIONS FOR TESTS
def _database_host() -> str | None:
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        try:
            raw = str(get_settings().database_url)
        except Exception:
            return None
    return urlparse(raw).hostname


def _auth_header(user_id: uuid.UUID) -> dict[str, str]:
    token = jwt.encode(
        {"sub": str(user_id), "aud": "authenticated"},
        get_settings().supabase_jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _insert_profile(user_id: uuid.UUID) -> None:
    with Session(get_engine()) as session:
        session.add(Profile(user_id=user_id, created_at=datetime.now(timezone.utc)))
        session.commit()


@pytest.fixture(scope="session")
def migrated_local_db() -> None:
    # same postgres as CI - skip if DATABASE_URL is Supabase or missing
    host = _database_host()
    if host not in {"localhost", "127.0.0.1"}:
        pytest.skip("create API tests use local or CI postgres")
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture
def client(migrated_local_db: None, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    def no_jwks() -> None:
        raise jwt.PyJWKClientError("tests use HS256")

    monkeypatch.setattr("hookitagain.api.auth._jwks_client", no_jwks)
    return TestClient(app)


@pytest.fixture
def signed_in(client: TestClient) -> tuple[TestClient, dict[str, str]]:
    user_id = uuid.uuid4()
    _insert_profile(user_id)
    return client, _auth_header(user_id)


def _create_project(client: TestClient, headers: dict[str, str]) -> dict:
    response = client.post("/api/projects", json={"name": "lab"}, headers=headers)
    assert response.status_code == 201
    return response.json()


def _create_https_destination(client: TestClient, headers: dict[str, str], project_id: str) -> dict:
    response = client.post(
        f"/api/projects/{project_id}/destinations",
        json={"name": "staging", "type": "https", "url": "https://example.com/hooks"},
        headers=headers,
    )
    assert response.status_code == 201
    return response.json()
# ------------------------------------------------------------------------------------------------- End of Helper Functions :)

def test_project_create_then_get(signed_in: tuple[TestClient, dict[str, str]]) -> None:
    client, headers = signed_in
    created = _create_project(client, headers)
    response = client.get(f"/api/projects/{created['id']}", headers=headers)
    assert response.status_code == 200
    assert response.json() == {
        "id": created["id"],
        "name": created["name"],
        "created_at": created["created_at"],
    }


def test_destination_signing_secret_only_on_create(
    signed_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = signed_in
    project = _create_project(client, headers)
    created = _create_https_destination(client, headers, project["id"])
    assert created["signing_secret"]

    listed = client.get(f"/api/projects/{project['id']}/destinations", headers=headers)
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert items
    for item in items:
        assert "signing_secret" not in item

    got = client.get(f"/api/destinations/{created['id']}", headers=headers)
    assert got.status_code == 200
    assert "signing_secret" not in got.json()


def test_source_secret_and_ingest_url_only_on_create(
    signed_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = signed_in
    project = _create_project(client, headers)
    destination = _create_https_destination(client, headers, project["id"])
    response = client.post(
        f"/api/projects/{project['id']}/sources",
        json={
            "name": "billing-webhooks",
            "provider": "generic_hmac",
            "destination_id": destination["id"],
        },
        headers=headers,
    )
    assert response.status_code == 201
    created = response.json()
    assert created["secret"]
    assert created["ingest_url"].endswith(f"/ingest/{created['id']}")

    listed = client.get(f"/api/projects/{project['id']}/sources", headers=headers)
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert items
    for item in items:
        assert "secret" not in item

    got = client.get(f"/api/sources/{created['id']}", headers=headers)
    assert got.status_code == 200
    assert "secret" not in got.json()


def test_rotate_replaces_stored_ciphertext(signed_in: tuple[TestClient, dict[str, str]]) -> None:
    client, headers = signed_in
    project = _create_project(client, headers)
    destination = _create_https_destination(client, headers, project["id"])
    old_signing_secret = destination["signing_secret"]

    rotated_destination = client.post(
        f"/api/destinations/{destination['id']}/rotate-signing-secret",
        headers=headers,
    )
    assert rotated_destination.status_code == 200
    new_signing_secret = rotated_destination.json()["signing_secret"]
    assert new_signing_secret != old_signing_secret
    with Session(get_engine()) as session:
        row = session.get(Destination, uuid.UUID(destination["id"]))
        assert row is not None
        assert decrypt_secret(row.signing_secret_ciphertext) == new_signing_secret

    source_response = client.post(
        f"/api/projects/{project['id']}/sources",
        json={
            "name": "billing-webhooks",
            "provider": "generic_hmac",
            "destination_id": destination["id"],
        },
        headers=headers,
    )
    assert source_response.status_code == 201
    source = source_response.json()
    old_secret = source["secret"]

    rotated_source = client.post(f"/api/sources/{source['id']}/rotate-secret", headers=headers)
    assert rotated_source.status_code == 200
    new_secret = rotated_source.json()["secret"]
    assert new_secret != old_secret
    with Session(get_engine()) as session:
        secret_row = session.get(SourceSecret, uuid.UUID(source["id"]))
        assert secret_row is not None
        assert decrypt_secret(secret_row.ciphertext) == new_secret


def test_cli_and_blocked_urls_are_422(signed_in: tuple[TestClient, dict[str, str]]) -> None:
    client, headers = signed_in
    project = _create_project(client, headers)
    path = f"/api/projects/{project['id']}/destinations"

    cli = client.post(path, json={"name": "local", "type": "cli"}, headers=headers)
    assert cli.status_code == 422
    assert cli.json()["error"]["code"] == "validation_error"

    http_url = client.post(
        path,
        json={"name": "staging", "type": "https", "url": "http://example.com/hooks"},
        headers=headers,
    )
    assert http_url.status_code == 422
    assert http_url.json()["error"]["code"] == "validation_error"

    loopback = client.post(
        path,
        json={"name": "staging", "type": "https", "url": "https://127.0.0.1/hooks"},
        headers=headers,
    )
    assert loopback.status_code == 422
    assert loopback.json()["error"]["code"] == "validation_error"


def test_other_users_ids_are_404(client: TestClient) -> None:
    owner_id = uuid.uuid4()
    other_id = uuid.uuid4()
    _insert_profile(owner_id)
    _insert_profile(other_id)
    owner = _auth_header(owner_id)
    other = _auth_header(other_id)

    project = _create_project(client, owner)
    destination = _create_https_destination(client, owner, project["id"])
    source_response = client.post(
        f"/api/projects/{project['id']}/sources",
        json={
            "name": "billing-webhooks",
            "provider": "generic_hmac",
            "destination_id": destination["id"],
        },
        headers=owner,
    )
    assert source_response.status_code == 201
    source = source_response.json()

    project_get = client.get(f"/api/projects/{project['id']}", headers=other)
    assert project_get.status_code == 404
    assert project_get.json()["error"]["code"] == "not_found"

    destination_get = client.get(f"/api/destinations/{destination['id']}", headers=other)
    assert destination_get.status_code == 404
    assert destination_get.json()["error"]["code"] == "not_found"

    source_get = client.get(f"/api/sources/{source['id']}", headers=other)
    assert source_get.status_code == 404
    assert source_get.json()["error"]["code"] == "not_found"


def test_missing_jwt_is_401() -> None:
    response = TestClient(app).get("/api/projects")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
