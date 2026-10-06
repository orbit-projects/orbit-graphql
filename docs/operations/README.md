# Orbit GraphQL: operations and security

This guide organizes runtime behavior documented by the package. It does not certify production readiness. Verify provider/client versions, permissions, transport security, limits, and failure behavior in the target environment before release.

## Configuration surface

Environment names found in the package README:

The package README does not name `ORBIT_*` variables. Use its typed constructors and application configuration, and confirm exact runtime inputs in the implementation before deployment.

Use the package README's constructor and deployment examples. Store credentials in a secret manager and avoid logging credentials, raw provider errors, request data, or opaque cursors.

## Lifecycle, failure behavior, and limits

`orbit-graphql` adds an optional GraphQL HTTP endpoint to an Orbit application through Core's
native plugin, router, authentication, request, and response contracts. It uses `graphql-core` as
the GraphQL specification parser, validator, and execution engine; it does not add another ASGI
server or web framework. See the [GraphQL-core documentation](https://graphql-core-3.readthedocs.io/en/stable/).

## Install and configure

```bash
python -m pip install orbit-core orbit-graphql
```

Register a schema explicitly. Resolvers receive the Orbit `Request` in `info.context`, so they can
use request-scoped dependencies and the authenticated principal without a second HTTP runtime.

```python
from graphql import GraphQLField, GraphQLObjectType, GraphQLSchema, GraphQLString
from orbit import Application, ApplicationConfig
from orbit_graphql import GraphQLPlugin

query = GraphQLObjectType(
    "Query",
    {"hello": GraphQLField(GraphQLString, resolve=lambda _root, _info: "world")},
)
application = Application(ApplicationConfig(name="example"))
application.register_plugin(GraphQLPlugin(GraphQLSchema(query=query)))
```

The endpoint accepts POST requests at `/graphql`. The default route requires the
`orbit.graphql.execute` role, and schema introspection is disabled. Set `roles=()` only when the
route is intentionally public; keep field-level authorization in resolvers for data with different
access policies. Enable introspection explicitly for trusted development tooling when appropriate.

## Request and execution limits

Defaults cap the request body at 256 KiB, query text at 128 Ki characters, parser work at 5,000
tokens, selection nesting at 32 levels, variables at 10,000 JSON values and 64 levels, validation
errors at 25, execution time at five seconds, and response JSON at 1 MiB. The application may
select tighter limits within the package's published maxima. Duplicate JSON keys, non-finite
numbers, unknown request fields, invalid query documents, and non-JSON values are rejected or
returned as bounded GraphQL errors. Core buffers an ASGI body before calling the route, so set
Core's `ApplicationConfig.max_body_bytes` to 256 KiB or lower when using the default package limit.

GraphQL operation and resolver errors are returned using generic messages so query values, schema
details, and provider exception text are not copied into HTTP responses. Correlate failures using
Core's request diagnostics and application-owned logging. The execution deadline cancels async
resolvers; synchronous resolvers must remain nonblocking because Python cannot safely preempt a
resolver that blocks the event loop. A resolver must still enforce its own row, page-size, and
downstream-call budgets. Selection depth and parser-token limits bound query shape but do not
replace per-client rate limits, cost analysis, database quotas, or authorization.

## Scope and status

GraphQL execution runs in-process in Python, which keeps resolvers beside Core's request context,
DI, routing, lifecycle, and security. A separate TypeScript GraphQL service is a distinct variant
only if it implements the same capability behind an explicitly configured process or HTTP boundary;
Core does not detect or switch runtimes. This package includes no IDE, subscriptions, multipart
uploads, file upload transport, federation, persisted-query store, or live load evidence. It is
pre-alpha and supports Python 3.11 through 3.14.

## Production validation

Validate startup/shutdown cleanup, timeout and cancellation behavior, concurrency and payload bounds where applicable, secret rotation and least-privilege access, data durability, backup/restore, and failover against the selected provider. Do not infer distributed or durable guarantees from an in-process API or fake-client tests.
