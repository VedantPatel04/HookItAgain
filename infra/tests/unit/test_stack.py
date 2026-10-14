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
