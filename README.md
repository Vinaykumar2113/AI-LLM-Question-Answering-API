# Production AI Q&A API

A small production-oriented AI Question-Answering API built with Python/FastAPI, JWT authentication, Redis, PostgreSQL, Docker and an external LLM provider.

## Run locally with Docker

1. Copy `.env.example` to `.env`.
2. Add your real `OPENAI_API_KEY` and replace `JWT_SECRET` with a strong random secret.
3. Start the stack:

```bash
docker compose up --build
```

4. Open Swagger UI at `http://localhost:8000/docs`.

## API flow

- `POST /auth/login` — OAuth2 password-style login that returns a JWT.
- `POST /chat` — authenticated question to LLM, with timeout/retry, token metrics, Redis rate limiting and PostgreSQL persistence.
- `GET /health` — API, Redis and PostgreSQL health checks.
- `GET /metrics` — Prometheus metrics; admin role only.

Demo credentials are embedded only for this assessment; a real system should use an identity provider and persistent user store.

## Production architecture

Users → Load Balancer/API Gateway → Kubernetes/ECS FastAPI replicas → Redis + Queue → LLM Gateway → LLM APIs

PostgreSQL stores persistent application data. Redis handles distributed rate limiting and caching. A background queue handles long-running work. HPA scales API replicas based on CPU/memory and custom request/latency metrics.

## Reliability

The application uses bounded LLM timeouts, exponential backoff retries, provider error mapping, graceful Redis degradation and health checks. In production, the LLM Gateway should add circuit breakers, concurrency limits, provider routing/fallback, request budgets and observability.

## Scaling to 100–500 RPS

Run multiple stateless FastAPI replicas behind a load balancer. Use Kubernetes HPA for automatic scaling. Redis provides shared rate-limit counters so limits work across replicas. Queue asynchronous jobs instead of holding web workers for long-running tasks. Enforce LLM RPM, TPM and concurrency budgets at the gateway and use admission control/backpressure. PostgreSQL uses a connection pool and can be read-replicated if reporting load grows.

## Migration from one EC2 server to 10,000 users

1. Containerize the current app and externalize configuration/secrets.
2. Add PostgreSQL and Redis with backups and monitoring.
3. Deploy the container to ECS or Kubernetes behind a load balancer.
4. Run multiple replicas and introduce health checks and autoscaling.
5. Put an LLM Gateway in front of providers for retries, timeouts, quotas, concurrency and fallback.
6. Move long-running tasks to a queue/worker tier.
7. Run old and new stacks in parallel, validate with smoke/load tests, then shift traffic gradually using weighted routing/blue-green deployment.
8. Keep rollback available until the new path is stable.

## Security

No secrets are hard-coded in the container configuration. Secrets should be stored in AWS Secrets Manager, Kubernetes Secrets backed by an external secret manager, or an equivalent vault. Production authentication should use SSO/OIDC and RBAC.

## Trade-offs

- Redis improves shared state and speed but adds an operational dependency; rate limiting can degrade open when Redis is unavailable.
- Queues improve resilience for long jobs but add eventual consistency and operational complexity.
- Kubernetes provides strong autoscaling and deployment controls but is heavier than ECS for a small team.
- An LLM Gateway adds one service but centralizes provider limits, routing, cost controls and reliability logic.
