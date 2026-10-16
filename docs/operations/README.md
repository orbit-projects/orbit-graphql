# Orbit GraphQL operations

Register the plugin and schema explicitly during application composition. Core owns the ASGI
listener and body buffering; `orbit-graphql` contributes a POST endpoint and per-instance execution
limits. Set Core's `ApplicationConfig.max_body_bytes` no higher than the plugin's configured
`max_request_bytes` so Core does not buffer a larger request before GraphQL rejects it.

## Admission and query budgets

The defaults cap request bytes at 256 KiB, query length at 128 Ki characters, parser tokens at
5,000, document fields at 500, selection depth at 32, validation errors at 25, variables at 10,000
JSON values and 64 levels, active request handling at 64 concurrent requests, execution at five
seconds, and serialized response bytes at 1 MiB. Settings may be lowered; constructor hard maxima
are exported or enforced by the package.

Field count and depth are shape limits, not a full query-cost model. A resolver that returns an
unbounded collection or makes many database/API calls can still consume substantial resources with
a small query. Enforce page sizes, rows, resolver timeouts, downstream quotas, and field-level
authorization in the schema/application. Add per-client rate limits at the application/security
layer and size downstream pools for total service capacity.

The 64-request admission default is per plugin instance. Requests arriving while all slots are
occupied receive HTTP 503 immediately; they are not placed into an unbounded waiting queue. A
separate instance in each process has its own slots. Calculate total possible resolver concurrency
across worker and replica counts. Tune the limit against the database pool, downstream quotas, and
measured latency. There is no automatic retry for rejected or failed requests.

## Execution, response, and cancellation

Execution defaults to a five-second deadline. Timeout cancels async resolver awaits and returns a
sanitized GraphQL error. It cannot stop synchronous resolver code that blocks the event loop, and a
timed-out mutation may have completed a side effect. Keep resolvers nonblocking and mutations
idempotent where retries are possible.

The response serializer emits compact JSON and replaces resolver error text with generic messages.
If a response exceeds the cap, the result becomes a compact `Response too large` GraphQL error.
Correlate failures with Core request diagnostics and application-owned structured logs; do not log
raw query variables or credentials by default.

## Deployment checks

Validate authentication roles, field-level access, introspection policy, body limits, query-shape
limits, resolver pagination, concurrent request limits, cancellation, sync resolver behavior,
mutations with ambiguous timeouts, and response-size handling. Put TLS and per-client rate limiting
at the appropriate application/ingress layer. Local schema tests do not prove a production query
budget or database capacity.
