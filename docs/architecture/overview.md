# Orbit GraphQL architecture

`orbit-graphql` is an optional in-process GraphQL-over-HTTP endpoint implemented as a Core plugin.
It uses `graphql-core` to parse, validate, and execute a caller-supplied `GraphQLSchema`. It
registers a POST route through Core and passes the Core `Request` as GraphQL resolver context.
There is no second ASGI server or web framework.

```text
Core router/auth/DI ──> GraphQLPlugin ──> graphql-core ──> application resolvers
       Request ───────────────────────────────────────────> info.context
```

Core continues to own routing, authentication, request-body reception, dependency context,
application lifecycle, and transport errors. The plugin owns GraphQL JSON decoding, parser and
schema validation, execution limits, safe GraphQL response formatting, and a per-plugin in-process
admission bound. Resolvers own data access, field-level authorization, pagination, and downstream
resource budgets.

## Request pipeline

The endpoint accepts POST requests at the configured path (default `/graphql`). It requires an
`application/json` or `application/*+json` media type and an object with a query plus optional
variables and operation name. Unknown top-level fields, duplicate JSON keys, non-finite numbers,
excessive nesting/value count, and invalid GraphQL documents are rejected. Parsing and validation
errors do not echo query text or schema details.

The route requires the `orbit.graphql.execute` role by default. Setting `roles=()` explicitly makes
the route public; every resolver that reads protected data still needs field/resource authorization.
Introspection is disabled by default and can be enabled explicitly for a trusted environment.

## Resource boundaries

Default policy caps are: 256 KiB request body, 128 Ki query characters, 5,000 parser tokens, 500
fields across the parsed document, 32 nested selection sets, 10,000 variable values, 64 JSON
nesting levels, 25 validation errors, 64 concurrent in-process requests, five seconds execution,
and 1 MiB serialized response. Constructor settings can lower policy within each documented hard
ceiling. Core buffers the ASGI body before the plugin sees it, so Core's own body limit must be set
to at most the plugin request bound to avoid a larger upstream allocation.

Requests beyond the local concurrency slots receive HTTP 503 without being queued. Slots are
per-plugin object and per-process; deployments with multiple workers/replicas multiply aggregate
capacity. This is not per-client rate limiting. Field count limits prevent very wide documents from
calling unbounded resolvers, but they do not calculate query cost from aliases, schema cardinality,
database rows, fanout, or downstream work. Resolvers must apply row/page limits, authorization,
timeouts, and provider quotas.

## Error and cancellation semantics

Resolver failures become a generic GraphQL error; exception text and causes are not serialized.
Async execution is wrapped in a timeout, which cancels awaiting async resolvers. Cancellation from
the host propagates. Synchronous resolvers can block the event loop and cannot be safely preempted;
keep them nonblocking or move blocking work behind an explicitly managed executor. A timed-out
resolver may already have performed a side effect, so mutations need application-level
idempotency/transaction policy.

The plugin adds no subscriptions, uploads, persisted-query store, federation, schema registry, or
external service. The package is alpha; local tests do not establish hosted CI or deployment
capacity.
