from pathlib import Path

from aws_cdk import (
    BundlingOptions,
    CfnOutput,
    Duration,
    Stack,
    aws_events as events,
    aws_events_targets as events_targets,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_lambda_event_sources as lambda_event_sources,
    aws_secretsmanager as secretsmanager,
    aws_sqs as sqs,
)
from constructs import Construct

from hookitagain_infra.contracts import (
    INGEST_RESERVED_CONCURRENCY,
    INGEST_WORKER_MEMORY_MB,
    RECONCILER_RESERVED_CONCURRENCY,
    RECONCILER_SCHEDULE_SECONDS,
    SQS_BATCH_SIZE,
    SQS_MAX_RECEIVE_COUNT,
    SQS_VISIBILITY_TIMEOUT_SECONDS,
    WORKER_LAMBDA_TIMEOUT_SECONDS,
    WORKER_RESERVED_CONCURRENCY,
)

_REPO = Path(__file__).resolve().parents[2]
# GitHub's OIDC sub uses owner@id/repo@id (use_immutable_subject), not owner/repo
_GITHUB_OIDC_SUB = (
    "repo:VedantPatel04@72629546/HookItAgain@1375425722:ref:refs/heads/main"
)
_CDK_QUALIFIER = "hnb659fds"


class InfraStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # this is the dead letter queue for (outbox job ID) that SQS moved here
        # after five receives because the worker never deleted the message
        self.delivery_dlq = sqs.Queue(self, "DeliveryDlq", fifo=False)

        # queue for (outbox job ID). message is hidden for 30 seconds after a worker
        # receives it so two workers do not run it. if the worker deletes it the
        # message is gone, if not the same message reappears
        self.delivery_queue = sqs.Queue(
            self,
            "DeliveryQueue",
            fifo=False,
            visibility_timeout=Duration.seconds(SQS_VISIBILITY_TIMEOUT_SECONDS),
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=SQS_MAX_RECEIVE_COUNT,
                queue=self.delivery_dlq,
            ),
        )

        # Fernet key for BYTEA ciphertext. AWS fills a random string on create.
        # Replace it with a Fernet key (same value as Render SECRET_ENCRYPTION_KEY)
        # before source-create or ingest uses it.
        self.secret_encryption_key = secretsmanager.Secret(
            self,
            "SecretEncryptionKey",
            description="Fernet key for source and destination secret ciphertext",
        )
        # same Supavisor URL as Render DATABASE_URL. AWS fills a random string on create.
        # replace it with that URL before ingest or the worker opens Postgres
        self.database_url = secretsmanager.Secret(
            self,
            "DatabaseUrl",
            description="Supavisor DATABASE_URL for ingest, worker, and reconciler",
        )

        # src/hookitagain plus SQLAlchemy, psycopg, pydantic-settings, cryptography.
        # boto3 stays on the Lambda runtime. .env is not in the zip.
        lambda_code = lambda_.Code.from_asset(
            str(_REPO),
            bundling=BundlingOptions(
                image=lambda_.Runtime.PYTHON_3_12.bundling_image,
                command=[
                    "bash",
                    "-c",
                    (
                        "pip install -r infra/lambda-requirements.txt -t /asset-output"
                        " && cp -r src/hookitagain /asset-output/hookitagain"
                    ),
                ],
            ),
            exclude=[".env", ".git", ".git/**", "web", "web/**", "tests", "tests/**"],
        )
        # CloudFormation dynamic refs. not plaintext in git. same names as Render.
        lambda_env = {
            "DATABASE_URL": self.database_url.secret_value.unsafe_unwrap(),
            "SECRET_ENCRYPTION_KEY": self.secret_encryption_key.secret_value.unsafe_unwrap(),
            "DELIVERY_QUEUE_URL": self.delivery_queue.queue_url,
        }

        # this is the public receive path. provider POSTs a webhook, we save the
        # event and outbox job, then put the outbox job ID on the delivery queue
        self.ingest = lambda_.Function(
            self,
            "Ingest",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="hookitagain.ingest.handler.handle",
            code=lambda_code,
            memory_size=INGEST_WORKER_MEMORY_MB,
            reserved_concurrent_executions=INGEST_RESERVED_CONCURRENCY,
            environment=lambda_env,
        )
        # public HTTPS URL for ingest. AWS does not check a signature here, the handler does
        self.ingest_url = self.ingest.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
        )
        self.delivery_queue.grant_send_messages(self.ingest)

        # this is the worker. takes one outbox job ID from the delivery queue,
        # POSTs to the destination, deletes the message if the destination returns 2xx
        self.worker = lambda_.Function(
            self,
            "Worker",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="hookitagain.worker.handler.handle",
            code=lambda_code,
            memory_size=INGEST_WORKER_MEMORY_MB,
            timeout=Duration.seconds(WORKER_LAMBDA_TIMEOUT_SECONDS),
            reserved_concurrent_executions=WORKER_RESERVED_CONCURRENCY,
            environment=lambda_env,
        )
        # this is the event source for the worker. it listens to the delivery queue
        # and takes one outbox job ID, POSTs to the destination, deletes the message
        # if the destination returns 2xx
        self.worker.add_event_source(
            lambda_event_sources.SqsEventSource(
                self.delivery_queue,
                batch_size=SQS_BATCH_SIZE,
            )
        )

        # every minute: if an outbox job is in Postgres but never reached SQS,
        # put its job ID on the delivery queue
        self.reconciler = lambda_.Function(
            self,
            "Reconciler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="hookitagain.reconciler.handler.handle",
            code=lambda_code,
            reserved_concurrent_executions=RECONCILER_RESERVED_CONCURRENCY,
            environment=lambda_env,
        )
        # lets this Lambda put leftover outbox job IDs on the delivery queue
        self.delivery_queue.grant_send_messages(self.reconciler)
        events.Rule(
            self,
            "ReconcilerSchedule",
            schedule=events.Schedule.rate(
                Duration.seconds(RECONCILER_SCHEDULE_SECONDS)
            ),
            targets=[events_targets.LambdaFunction(self.reconciler)],
        )

        self.secret_encryption_key.grant_read(self.ingest)
        self.secret_encryption_key.grant_read(self.worker)
        self.secret_encryption_key.grant_read(self.reconciler)
        self.database_url.grant_read(self.ingest)
        self.database_url.grant_read(self.worker)
        self.database_url.grant_read(self.reconciler)

        CfnOutput(self, "IngestUrl", value=self.ingest_url.url)
        CfnOutput(
            self,
            "SecretEncryptionKeyArn",
            value=self.secret_encryption_key.secret_arn,
        )
        CfnOutput(
            self,
            "DatabaseUrlArn",
            value=self.database_url.secret_arn,
        )

        # GitHub OIDC so Actions can assume a role without long-lived access keys
        github_oidc = iam.OpenIdConnectProvider(
            self,
            "GitHubOidc",
            url="https://token.actions.githubusercontent.com",
            client_ids=["sts.amazonaws.com"],
        )
        # this is the role main's deploy job assumes. only that repo and ref,
        # and it may only assume the CDK bootstrap roles
        self.github_deploy_role = iam.Role(
            self,
            "GitHubDeploy",
            assumed_by=iam.WebIdentityPrincipal(
                github_oidc.open_id_connect_provider_arn,
                conditions={
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                    },
                    "StringLike": {
                        "token.actions.githubusercontent.com:sub": _GITHUB_OIDC_SUB,
                    },
                },
            ),
        )
        bootstrap_account_region = f"{self.account}-{self.region}"
        for kind in (
            "deploy-role",
            "file-publishing-role",
            "image-publishing-role",
            "lookup-role",
        ):
            self.github_deploy_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["sts:AssumeRole"],
                    resources=[
                        f"arn:aws:iam::{self.account}:role/cdk-{_CDK_QUALIFIER}-{kind}-{bootstrap_account_region}"
                    ],
                )
            )
        CfnOutput(
            self,
            "GitHubDeployRoleArn",
            value=self.github_deploy_role.role_arn,
        )


# deploy with `cdk deploy`
