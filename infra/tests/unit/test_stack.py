import json

from aws_cdk import App
from aws_cdk.assertions import Template

from hookitagain_infra.stack import InfraStack


def _template() -> Template:
    app = App()
    stack = InfraStack(app, "dev")
    return Template.from_stack(stack)


def _function_by_handler(template: Template, handler: str) -> dict:
    functions = template.find_resources("AWS::Lambda::Function")
    matches = [
        resource
        for resource in functions.values()
        if resource["Properties"].get("Handler") == handler
    ]
    assert len(matches) == 1
    return matches[0]["Properties"]


def test_ingest_function_url_and_concurrency() -> None:
    template = _template()
    ingest = _function_by_handler(template, "hookitagain.ingest.handler.handle")
    assert ingest["MemorySize"] == 256
    assert ingest["ReservedConcurrentExecutions"] == 5
    assert "Timeout" not in ingest
    template.has_resource_properties(
        "AWS::Lambda::Url",
        {"AuthType": "NONE"},
    )


def test_worker_timeout_batch_and_concurrency() -> None:
    template = _template()
    worker = _function_by_handler(template, "hookitagain.worker.handler.handle")
    assert worker["MemorySize"] == 256
    assert worker["Timeout"] == 20
    assert worker["ReservedConcurrentExecutions"] == 2
    template.has_resource_properties(
        "AWS::Lambda::EventSourceMapping",
        {"BatchSize": 1},
    )


def test_reconciler_schedule_and_concurrency() -> None:
    template = _template()
    reconciler = _function_by_handler(
        template, "hookitagain.reconciler.handler.handle"
    )
    assert reconciler["ReservedConcurrentExecutions"] == 1
    assert "MemorySize" not in reconciler
    assert "Timeout" not in reconciler
    template.has_resource_properties(
        "AWS::Events::Rule",
        {"ScheduleExpression": "rate(1 minute)"},
    )


def test_github_oidc_role_trusts_main_only() -> None:
    template = _template()
    oidc_roles = [
        resource
        for resource in template.find_resources("AWS::IAM::Role").values()
        if "AssumeRoleWithWebIdentity" in json.dumps(resource)
        and "token.actions.githubusercontent.com" in json.dumps(resource)
    ]
    assert len(oidc_roles) == 1
    blob = json.dumps(oidc_roles[0])
    assert "VedantPatel04@72629546/HookItAgain@1375425722" in blob
    assert "refs/heads/main" in blob
    assert "repo:VedantPatel04/HookItAgain:ref:" not in blob
    assert "AdministratorAccess" not in blob
    assert "sts:AssumeRole" in blob
