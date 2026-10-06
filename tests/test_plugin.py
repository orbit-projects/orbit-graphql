from __future__ import annotations

import asyncio
import json

import pytest
from graphql import (
    GraphQLField,
    GraphQLObjectType,
    GraphQLResolveInfo,
    GraphQLSchema,
    GraphQLString,
)
from orbit import Application, ApplicationConfig
from orbit.asgi import ASGIApplication
from orbit_testing import TestClient

from orbit_graphql import GraphQLPlugin


def schema_for(resolve_greeting: object = None) -> GraphQLSchema:
    async def greeting(_root: object, info: GraphQLResolveInfo) -> str:
        request = info.context
        return f"hello:{request.method}"

    resolver = resolve_greeting if callable(resolve_greeting) else greeting
    query = GraphQLObjectType(
        "Query",
        {
            "greeting": GraphQLField(GraphQLString, resolve=resolver),
            "child": GraphQLField(GraphQLString, resolve=lambda *_args: "child"),
        },
    )
    return GraphQLSchema(query=query)


def make_application(plugin: GraphQLPlugin) -> Application:
    application = Application(ApplicationConfig(name="graphql-tests"))
    application.register_plugin(plugin)
    return application


async def execute(
    plugin: GraphQLPlugin,
    body: bytes,
    *,
    content_type: str = "application/json",
) -> tuple[int, dict[str, object]]:
    application = make_application(plugin)
    async with TestClient(ASGIApplication(application)) as client:
        response = await client.request(
            "POST",
            "/graphql",
            body=body,
            headers={"content-type": content_type},
        )
    return response.status, response.json()


@pytest.mark.asyncio
async def test_executes_async_resolvers_with_the_core_request_as_context() -> None:
    plugin = GraphQLPlugin(schema_for(), roles=())

    status, result = await execute(plugin, b'{"query":"{ greeting }"}')

    assert status == 200
    assert result == {"data": {"greeting": "hello:POST"}}


@pytest.mark.asyncio
async def test_variables_and_operation_name_are_passed_to_graphql_execution() -> None:
    query = GraphQLObjectType(
        "Query",
        {
            "greeting": GraphQLField(
                GraphQLString,
                resolve=lambda _root, _info, name: f"hello:{name}",
                args={"name": GraphQLString},
            )
        },
    )
    plugin = GraphQLPlugin(GraphQLSchema(query=query), roles=())
    body = (
        b'{"query":"query Hi($name: String!) { greeting(name: $name) }",'
        b'"variables":{"name":"Ada"},"operationName":"Hi"}'
    )

    status, result = await execute(plugin, body)

    assert status == 200
    assert result == {"data": {"greeting": "hello:Ada"}}


@pytest.mark.asyncio
async def test_duplicate_json_keys_and_invalid_json_are_rejected() -> None:
    plugin = GraphQLPlugin(schema_for(), roles=())

    duplicate_status, duplicate = await execute(
        plugin, b'{"query":"{ greeting }","query":"{ child }"}'
    )
    invalid_status, invalid = await execute(plugin, b"{", content_type="application/json")

    assert duplicate_status == 400
    assert duplicate["code"] == "graphql.invalid-request"
    assert invalid_status == 400


@pytest.mark.asyncio
async def test_wrong_content_type_and_oversized_request_fail_before_execution() -> None:
    plugin = GraphQLPlugin(schema_for(), roles=(), max_request_bytes=32)

    media_status, media = await execute(
        plugin, b'{"query":"{ greeting }"}', content_type="text/plain"
    )
    size_status, size = await execute(plugin, b'{"query":"{' + b" " * 32 + b' }"}')

    assert media_status == 415
    assert media["code"] == "graphql.media-type"
    assert size_status == 413
    assert size["code"] == "graphql.request-too-large"


@pytest.mark.asyncio
async def test_default_introspection_and_excessive_selection_depth_are_denied() -> None:
    introspection = GraphQLPlugin(schema_for(), roles=())
    depth = GraphQLPlugin(schema_for(), roles=(), max_depth=1)

    _, introspection_result = await execute(
        introspection, b'{"query":"{ __schema { types { name } } }"}'
    )
    _, depth_result = await execute(depth, b'{"query":"{ child { child { greeting } } }"}')

    assert introspection_result["errors"]
    assert depth_result["errors"]
    assert "introspection" not in json.dumps(introspection_result).lower()


@pytest.mark.asyncio
async def test_default_route_requires_core_authentication() -> None:
    plugin = GraphQLPlugin(schema_for())

    status, result = await execute(plugin, b'{"query":"{ greeting }"}')

    assert status == 401
    assert result["code"] == "security.forbidden"


@pytest.mark.asyncio
async def test_resolver_exception_text_is_not_exposed() -> None:
    def fail(*_args: object) -> str:
        raise RuntimeError("password=top-secret")

    plugin = GraphQLPlugin(schema_for(fail), roles=())

    status, result = await execute(plugin, b'{"query":"{ greeting }"}')

    assert status == 200
    assert "top-secret" not in json.dumps(result)
    assert result["errors"] == [{"message": "GraphQL operation returned an error."}]


@pytest.mark.asyncio
async def test_execution_timeout_cancels_async_resolver() -> None:
    cancelled = asyncio.Event()

    async def slow(*_args: object) -> str:
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return "late"

    plugin = GraphQLPlugin(schema_for(slow), roles=(), execution_timeout=0.02)

    status, result = await execute(plugin, b'{"query":"{ greeting }"}')

    assert status == 200
    assert result["errors"] == [{"message": "GraphQL execution timed out."}]
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_response_size_is_bounded_and_query_errors_do_not_echo_input() -> None:
    plugin = GraphQLPlugin(schema_for(lambda *_args: "x" * 300), roles=(), max_response_bytes=128)

    status, result = await execute(plugin, b'{"query":"{ greeting }"}')

    assert status == 200
    assert len(json.dumps(result).encode()) < 128
    _, error_result = await execute(plugin, b'{"query":"{ missing }"}')
    assert "missing" not in json.dumps(error_result)


def test_default_role_is_required_and_configuration_limits_are_strict() -> None:
    plugin = GraphQLPlugin(schema_for())
    application = make_application(plugin)

    assert application.plugins.ordered()
    assert plugin.roles == frozenset({"orbit.graphql.execute"})
    with pytest.raises(ValueError, match="integer"):
        GraphQLPlugin(schema_for(), max_tokens=True)  # type: ignore[arg-type]
