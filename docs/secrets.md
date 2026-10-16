# Secrets and HMAC

HMAC-SHA256 is a value computed using bytes of payload and source-secret by the provider, inserted as X-Webhook-Signature-256 into header. Ingestion service decrypts copy of source-secret written into Postgres by API, computes HMAC over payload and verifies with HMAC in header from the provider.

 Fernet Encryption is how we store the two HMAC secrets in Postgres. `SECRET_ENCRYPTION_KEY` is the Fernet process key. It is not an HMAC secret.

## Create

FastAPI creates both secrets. The provider does not.

On destination create, FastAPI generates a destination signing secret. It encrypts that value into `destinations.signing_secret_ciphertext`. `destinations.url` stays plaintext so the worker can POST there.

On source create, FastAPI generates a source secret. It encrypts that value into `source_secrets.ciphertext`. The ingest URL is `POST /ingest/{source_id}` and does not carry ciphertext.

Each create response returns plaintext once. List APIs never decrypt.

## Inbound

The provider keeps the source secret we returned. On each POST it sends `X-Webhook-Signature-256`, which is HMAC of the raw body with that source secret.

Ingest loads our copy of the source secret and decrypts it. It runs the same HMAC on the same body. A match means the sender knew the secret and the body was not altered. Ingest then writes Event, Delivery, and OutboxJob. It does not decrypt the destination signing secret.

## Outbound

The worker decrypts the destination signing secret. It POSTs the unmodified Event body to `destinations.url`. It signs with HMAC of `{timestamp}.{raw_body}` using the destination signing secret. That secret is not the source secret.

The destination can verify that header the same way ingest verified the inbound one.
