#!/usr/bin/env python3
import os

import aws_cdk as cdk

from hookitagain_infra.stack import InfraStack

ALLOWED_STAGES = frozenset({"dev", "demo"})


def resolve_stage(app: cdk.App) -> str:
    stage = app.node.try_get_context("stage") or "dev"
    if stage not in ALLOWED_STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    return stage


def main() -> None:
    app = cdk.App()
    stage = resolve_stage(app)
    InfraStack(
        app,
        stage,
        env=cdk.Environment(
            account=os.getenv("CDK_DEFAULT_ACCOUNT"),
            region=os.getenv("CDK_DEFAULT_REGION"),
        ),
    )
    app.synth()


if __name__ == "__main__":
    main()
