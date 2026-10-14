import pytest
from aws_cdk import App

from app import resolve_stage


def test_resolve_stage_defaults_to_dev() -> None:
    app = App()
    assert resolve_stage(app) == "dev"


def test_resolve_stage_accepts_demo() -> None:
    app = App(context={"stage": "demo"})
    assert resolve_stage(app) == "demo"


def test_resolve_stage_rejects_unknown_stage() -> None:
    app = App(context={"stage": "prod"})
    with pytest.raises(ValueError, match="unknown stage 'prod'"):
        resolve_stage(app)
