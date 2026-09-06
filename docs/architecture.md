# Architecture, scaling and migration

## Production request path

```text
Users
  |
Load Balancer / API Gateway
  |
FastAPI replicas (stateless)
  |------ Redis: distributed rate limits / cache
  |------ PostgreSQL: durable application data
  |
LLM Gateway
  |------ RPM / TPM / concurrency limits
  |------ timeout / retry / circuit breaker
  |------ provider routing / fallback
  |
LLM Providers
```

## 100 RPS with 500 RPS bursts

- Keep FastAPI instances stateless and scale horizontally behind a load balancer.
- Use Kubernetes HPA or ECS autoscaling based on CPU, memory and custom request/latency signals.
- Use Redis for shared rate-limit counters across replicas.
- Move long-running or asynchronous work to a queue/worker tier.
- Enforce LLM RPM, TPM and concurrency budgets at an LLM Gateway, with admission control/backpressure.
- Protect PostgreSQL with connection pooling and add read replicas only when reporting load requires them.

## Failure recovery

- LLM timeout: bounded retries with exponential backoff, then `504`.
- Provider error: retry, then `502`; production gateway can route to a fallback provider.
- Redis outage: this assessment implementation degrades rate limiting rather than blocking the core API; production may choose fail-closed behavior.
- API instance failure: health checks remove the instance and the load balancer routes to healthy replicas.
- Database failure: health becomes degraded; managed DB failover and backups are recommended.

## EC2 to 10,000 users

1. Containerize the service and externalize configuration/secrets.
2. Provision managed PostgreSQL and Redis with backups and monitoring.
3. Deploy multiple API replicas behind a load balancer.
4. Add health checks and autoscaling.
5. Introduce the LLM Gateway for quotas, concurrency, retries, timeouts and fallback.
6. Move long-running work to queues/workers.
7. Run old and new stacks in parallel and validate with smoke/load tests.
8. Shift traffic gradually with weighted routing or blue/green deployment.
9. Keep rollback available until the new path is stable.

This minimizes downtime because the old and new paths operate concurrently during cutover.
