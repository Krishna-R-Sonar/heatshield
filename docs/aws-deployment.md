# AWS Deployment (Phase 10+)

## Recommended Target Topology (Not Deployed)

- **CloudFront + private S3** — static `frontend/dist`
- **API Gateway HTTP API + Lambda/Mangum** — `/api/v1/*`; use ECS Fargate behind an ALB if long-running work or sustained traffic requires it
- **EventBridge Scheduler + Step Functions** — source-specific ingestion, validation, retries, and orchestration
- **SQS + dead-letter queue** — isolate transient provider failures
- **S3 raw archive** — immutable source payloads for audit and replay
- **DynamoDB** — normalized observations keyed by location/source/time, latest snapshots, assessments, and alerts
- **Versioned `heatshield_risk` package** — deterministic scoring; persist engine version and input provenance with each assessment
- **Secrets Manager** — model credentials; Bedrock is the AWS-native option, or retain OpenRouter behind a server-side proxy
- **CloudWatch + KMS + least-privilege IAM** — service observability, encryption, and access control

## Not in MVP

- SNS (only after an approved notification policy and verified warning source exist)
- Any production ingestion provider or cloud resource

## Local Parity

Provider adapters should normalize each feed to the common source/timestamp/location/value/unit/quality envelope. Reject or mark stale/invalid records instead of treating them as current observations. Keep synthetic and simulated records distinct from integrated observations.

No AWS deployment has been performed. Create infrastructure-as-code, define source licensing and freshness/quality policy, and validate the local-to-cloud data contract before any deployment.
