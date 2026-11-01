import base64
import json

import pytest

from hookitagain.ingest import handler as ingest_handler
from hookitagain.ingest.handler import handle


def test_missing_delivery_id_is_400() -> None:
    response = handle(
        {
            "rawPath": "/ingest/b7658768-e9af-45d2-9ea3-86ce01b493a2",
            "headers": {"x-webhook-signature-256": "sha256=abc"},
            "body": "{}",
        },
        None,
    )
    assert response["statusCode"] == 400
    assert json.loads(response["body"])["error"]["code"] == "missing_delivery_id"


def test_missing_signature_is_401() -> None:
    response = handle(
        {
            "rawPath": "/ingest/b7658768-e9af-45d2-9ea3-86ce01b493a2",
            "headers": {"x-webhook-id": "deliv-1"},
            "body": "{}",
        },
        None,
    )
    assert response["statusCode"] == 401
    assert json.loads(response["body"])["error"]["code"] == "signature_invalid"


def test_oversize_body_is_413() -> None:
    response = handle(
        {
            "rawPath": "/ingest/b7658768-e9af-45d2-9ea3-86ce01b493a2",
            "headers": {
                "x-webhook-id": "deliv-1",
                "x-webhook-signature-256": "sha256=abc",
            },
            "body": "x" * 65537,
        },
        None,
    )
    assert response["statusCode"] == 413
    assert json.loads(response["body"])["error"]["code"] == "payload_too_large"


def test_bad_path_is_source_not_found() -> None:
    response = handle(
        {
            "rawPath": "/ingest/not-a-uuid",
            "headers": {
                "x-webhook-id": "deliv-1",
                "x-webhook-signature-256": "sha256=abc",
            },
            "body": "{}",
        },
        None,
    )
    assert response["statusCode"] == 400
    assert json.loads(response["body"])["error"]["code"] == "source_not_found"


def test_empty_delivery_id_is_400() -> None:
    response = handle(
        {
            "rawPath": "/ingest/b7658768-e9af-45d2-9ea3-86ce01b493a2",
            "headers": {
                "x-webhook-id": "   ",
                "x-webhook-signature-256": "sha256=abc",
            },
            "body": "{}",
        },
        None,
    )
    assert response["statusCode"] == 400
    assert json.loads(response["body"])["error"]["code"] == "missing_delivery_id"


def test_body_at_65536_is_not_413(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_ingest(*args, **kwargs) -> dict:
        captured["raw_body"] = args[1]
        return {"statusCode": 202, "body": "{}"}

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(ingest_handler, "get_lambda_settings", lambda: object())
    monkeypatch.setattr(ingest_handler, "get_engine", lambda: None)
    monkeypatch.setattr(ingest_handler, "Session", lambda engine: _Session())
    monkeypatch.setattr(ingest_handler, "ingest", fake_ingest)

    response = handle(
        {
            "rawPath": "/ingest/b7658768-e9af-45d2-9ea3-86ce01b493a2",
            "headers": {
                "x-webhook-id": "deliv-1",
                "x-webhook-signature-256": "sha256=abc",
            },
            "body": "x" * 65536,
        },
        None,
    )
    assert response["statusCode"] == 202
    assert captured["raw_body"] == b"x" * 65536


def test_base64_body_is_decoded_before_ingest(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_ingest(*args, **kwargs) -> dict:
        captured["raw_body"] = args[1]
        return {"statusCode": 202, "body": "{}"}

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(ingest_handler, "get_lambda_settings", lambda: object())
    monkeypatch.setattr(ingest_handler, "get_engine", lambda: None)
    monkeypatch.setattr(ingest_handler, "Session", lambda engine: _Session())
    monkeypatch.setattr(ingest_handler, "ingest", fake_ingest)

    raw = b'{"ok":true}'
    response = handle(
        {
            "rawPath": "/ingest/b7658768-e9af-45d2-9ea3-86ce01b493a2",
            "headers": {
                "x-webhook-id": "deliv-1",
                "x-webhook-signature-256": "sha256=abc",
            },
            "body": base64.b64encode(raw).decode("ascii"),
            "isBase64Encoded": True,
        },
        None,
    )
    assert response["statusCode"] == 202
    assert captured["raw_body"] == raw
