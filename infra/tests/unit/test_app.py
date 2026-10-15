import pytest
from aws_cdk import App

from app import resolve_stage


@pytest.mark.parametrize(
    "context, expected",
    [
        ({}, "dev"),
        ({"stage": "demo"}, "demo"),
    ],
)
def test_resolve_stage_only_allows_dev_and_demo(
    context: dict, expected: str
) -> None:
    assert resolve_stage(App(context=context)) == expected


def test_resolve_stage_rejects_unknown_stage() -> None:
    app = App(context={"stage": "prod"})
    with pytest.raises(ValueError, match="unknown stage 'prod'"):
        resolve_stage(app)
