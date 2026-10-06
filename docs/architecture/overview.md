# Orbit GraphQL: architecture and boundaries

## Responsibility

`orbit-graphql` adds an optional GraphQL HTTP endpoint to an Orbit application through Core's
native plugin, router, authentication, request, and response contracts. It uses `graphql-core` as
the GraphQL specification parser, validator, and execution engine; it does not add another ASGI
server or web framework. See the [GraphQL-core documentation](https://graphql-core-3.readthedocs.io/en/stable/).

## Declared dependencies

The following dependency declarations come from the checked-in manifests. Optional groups and development dependencies are called out separately.

### `pyproject.toml`
- `graphql-core>=3.2.12,<3.3`
- `orbit-core>=0.1.0a1,<0.2`
- `pydantic>=2.8,<3`
- Optional `dev` group: `pytest>=8,<10`, `pytest-asyncio>=0.24,<2`, `ruff>=0.8,<1`, `mypy>=1.13,<2`, `orbit-testing>=0.1.0a1,<0.2`.

Declared dependencies do not mean that optional providers or services are bundled with this package.

## Implementation layout

Representative implementation files in this checkout:

- `src/orbit_graphql/__init__.py`
- `src/orbit_graphql/plugin.py`

## Public contract and scope

## Scope and status

GraphQL execution runs in-process in Python, which keeps resolvers beside Core's request context,
DI, routing, lifecycle, and security. A separate TypeScript GraphQL service is a distinct variant
only if it implements the same capability behind an explicitly configured process or HTTP boundary;
Core does not detect or switch runtimes. This package includes no IDE, subscriptions, multipart
uploads, file upload transport, federation, persisted-query store, or live load evidence. It is
pre-alpha and supports Python 3.11 through 3.14.

## Boundary rules

Keep provider SDKs, credentials, transports, and provider-specific error translation in provider adapters. Keep reusable capability contracts in the matching capability package and lifecycle orchestration in Core. Apply the relevant layer for this repository and preserve the dependency direction shown above.
