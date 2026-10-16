# Orbit GraphQL security guidance

GraphQL exposes application-defined schema and resolver behavior over HTTP. The plugin supplies a
bounded request pipeline and Core route authorization; it does not make an unsafe schema safe.

## Authentication and authorization

The route requires `orbit.graphql.execute` by default. `roles=()` removes that route-level gate and
is an explicit public exposure decision. Enforce authorization again at fields/resources whose
policies differ. Resolver context is the Core `Request`, so use the application-approved principal
and request-scoped dependencies. Do not trust IDs or tenant values supplied in GraphQL arguments as
proof of access.

Introspection is disabled by default. If enabled, consider whether schema and field metadata should
be visible in the deployed environment. Introspection policy is not data authorization. Core route
roles and resolver checks remain necessary.

## Resource abuse

The package limits body/query size, parser tokens, selected document field count, selection depth,
variable count/nesting, validation error count, concurrent requests per process, execution time,
and response bytes. This reduces common parser and fanout abuse but is not a per-client rate limiter
or complete cost analyzer. Repeated aliases may execute a field multiple times; nested resolvers can
amplify downstream reads. Apply ingress/application rate limits, pagination, row budgets, resolver
timeouts, concurrency limits for downstream pools, and authorization checks.

Core buffers request bodies before this route runs. Align Core's body bound with the plugin's bound
to avoid accepting a larger allocation earlier in the request path. Capacity controls are per
process and multiply across replicas.

## Sensitive data and resolver behavior

Variables and query text can contain personal data or secrets. Invalid-request and resolver error
responses are sanitized, but the plugin does not prevent application resolvers, Core diagnostics,
database drivers, or tracing hooks from logging them. Redact variables and credentials in logs,
metrics, and traces, and avoid secrets in query arguments.

Async resolver timeouts propagate cancellation. Resolvers should clean up resources in `finally`
blocks. Synchronous resolvers are not preempted and can block the event loop. A mutation may perform
an external side effect before a timeout or client disconnect; use transactions and idempotency
appropriate to the operation.

## Transport and scope

Deploy behind TLS and trusted ingress, and configure forwarded client identity only through Core's
trusted-proxy policy. The plugin does not provide CSRF protection, persisted-query controls,
subscriptions, uploads, schema federation, or per-client rate limiting. Follow the repository
[security policy](../../SECURITY.md) for vulnerability reporting; do not post credentials, real
variables, or sensitive schema details in public issues.
