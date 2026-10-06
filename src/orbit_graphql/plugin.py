# Copyright 2026-present Orbit Contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Optional bounded GraphQL endpoint implemented on Orbit's native route contract."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Mapping
from typing import Any, cast

from graphql import (
    ASTValidationRule,
    ExecutionResult,
    GraphQLError,
    GraphQLSchema,
    assert_valid_schema,
    execute,
    parse,
    validate,
)
from graphql.language import SelectionSetNode
from graphql.validation import NoSchemaIntrospectionCustomRule, specified_rules
from orbit.application import Application
from orbit.asgi import Headers, Request, Response
from orbit.asgi.request import HTTPError
from orbit.plugins import Plugin
from orbit.plugins.metadata import PluginMetadata
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError

MAX_GRAPHQL_REQUEST_BYTES = 262_144
MAX_GRAPHQL_QUERY_CHARACTERS = 131_072
MAX_GRAPHQL_VARIABLE_VALUES = 10_000
MAX_GRAPHQL_DEPTH = 32
MAX_GRAPHQL_TOKENS = 5_000
MAX_GRAPHQL_ERRORS = 25
MAX_GRAPHQL_TIMEOUT_SECONDS = 30.0
MAX_GRAPHQL_RESPONSE_BYTES = 1_048_576
_MAX_JSON_DEPTH = 64


class _GraphQLRequest(BaseModel):
    """Strict bounded shape of the GraphQL-over-HTTP JSON request body."""

    model_config = ConfigDict(strict=True, extra="forbid", populate_by_name=True)

    query: StrictStr = Field(min_length=1, max_length=MAX_GRAPHQL_QUERY_CHARACTERS)
    variables: dict[str, object] | None = None
    operation_name: StrictStr | None = Field(default=None, alias="operationName")


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate JSON keys to keep request meaning unambiguous."""
    output: dict[str, object] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("Duplicate JSON keys are not allowed.")
        output[key] = value
    return output


def _no_non_finite(value: str) -> object:
    """Reject non-standard JSON numeric constants."""
    del value
    raise ValueError("Non-finite JSON numbers are not allowed.")


def _validate_json_values(value: object, *, depth: int = 0, count: list[int] | None = None) -> None:
    """Limit recursive user variables before GraphQL input coercion begins."""
    if count is None:
        count = [0]
    count[0] += 1
    if count[0] > MAX_GRAPHQL_VARIABLE_VALUES:
        raise ValueError("GraphQL variables contain too many values.")
    if isinstance(value, dict):
        if depth >= _MAX_JSON_DEPTH:
            raise ValueError("GraphQL variables are nested too deeply.")
        for key, child in value.items():
            if (
                not isinstance(key, str)
                or not 1 <= len(key) <= 255
                or any(ord(character) < 32 or ord(character) == 127 for character in key)
            ):
                raise ValueError("GraphQL variables contain an invalid key.")
            _validate_json_values(child, depth=depth + 1, count=count)
    elif isinstance(value, list):
        if depth >= _MAX_JSON_DEPTH:
            raise ValueError("GraphQL variables are nested too deeply.")
        for child in value:
            _validate_json_values(child, depth=depth + 1, count=count)
    elif (
        value is None
        or isinstance(value, (bool, str, int))
        or (isinstance(value, float) and math.isfinite(value))
    ):
        return
    else:
        raise ValueError("GraphQL variables contain an invalid value.")


def _depth_validation_rule(max_depth: int) -> type[ASTValidationRule]:
    """Create a validator that bounds nested selection sets for one plugin configuration."""

    class SelectionDepthRule(ASTValidationRule):
        """Reject operations and fragments whose nested selection structure is too deep."""

        def __init__(self, context: Any) -> None:
            super().__init__(context)
            self._depth = 0

        def enter_selection_set(self, node: SelectionSetNode, *_args: object) -> None:
            """Count nested field selection sets during AST validation."""
            self._depth += 1
            if self._depth > max_depth:
                self.context.report_error(
                    GraphQLError("GraphQL selection nesting exceeds the configured limit.", node)
                )

        def leave_selection_set(self, _node: SelectionSetNode, *_args: object) -> None:
            """Restore depth as the validation visitor leaves a selection set."""
            self._depth -= 1

    return SelectionDepthRule


class GraphQLPlugin(Plugin):
    """Register a POST-only GraphQL endpoint over Core routing and authorization.

    Resolvers receive the Core :class:`Request` as ``info.context``. The default route requires
    the ``orbit.graphql.execute`` role; set ``roles=()`` only when the application deliberately
    exposes this schema publicly. Introspection is disabled unless explicitly enabled.
    """

    metadata = PluginMetadata(
        name="orbit-graphql",
        version="0.1.0a1",
        capabilities=frozenset({"graphql.execute"}),
    )

    def __init__(
        self,
        schema: GraphQLSchema,
        *,
        path: str = "/graphql",
        roles: tuple[str, ...] = ("orbit.graphql.execute",),
        allow_introspection: bool = False,
        max_request_bytes: int = MAX_GRAPHQL_REQUEST_BYTES,
        max_query_characters: int = MAX_GRAPHQL_QUERY_CHARACTERS,
        max_tokens: int = MAX_GRAPHQL_TOKENS,
        max_depth: int = MAX_GRAPHQL_DEPTH,
        max_errors: int = MAX_GRAPHQL_ERRORS,
        execution_timeout: float = 5.0,
        max_response_bytes: int = MAX_GRAPHQL_RESPONSE_BYTES,
    ) -> None:
        """Validate schema, authorization and resource ceilings at plugin construction."""
        if not isinstance(schema, GraphQLSchema):
            raise TypeError("GraphQLPlugin requires a GraphQLSchema instance.")
        assert_valid_schema(schema)
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError("GraphQL path must be an absolute route path.")
        if not isinstance(roles, tuple) or any(not isinstance(role, str) for role in roles):
            raise TypeError("GraphQL roles must be a tuple of role names.")
        if len(roles) > 32 or len(roles) != len(set(roles)):
            raise ValueError("GraphQL roles must be bounded and unique.")
        if not isinstance(allow_introspection, bool):
            raise TypeError("allow_introspection must be a boolean.")
        self._max_request_bytes = self._bounded_int(
            "max_request_bytes", max_request_bytes, 1, MAX_GRAPHQL_REQUEST_BYTES
        )
        self._max_query_characters = self._bounded_int(
            "max_query_characters", max_query_characters, 1, MAX_GRAPHQL_QUERY_CHARACTERS
        )
        self._max_tokens = self._bounded_int("max_tokens", max_tokens, 1, MAX_GRAPHQL_TOKENS)
        self._max_depth = self._bounded_int("max_depth", max_depth, 1, MAX_GRAPHQL_DEPTH)
        self._max_errors = self._bounded_int("max_errors", max_errors, 1, MAX_GRAPHQL_ERRORS)
        self._max_response_bytes = self._bounded_int(
            "max_response_bytes", max_response_bytes, 128, MAX_GRAPHQL_RESPONSE_BYTES
        )
        if (
            isinstance(execution_timeout, bool)
            or not isinstance(execution_timeout, (int, float))
            or not math.isfinite(execution_timeout)
            or not 0.01 <= execution_timeout <= MAX_GRAPHQL_TIMEOUT_SECONDS
        ):
            raise ValueError(
                f"execution_timeout must be between 0.01 and {MAX_GRAPHQL_TIMEOUT_SECONDS} seconds."
            )
        self.schema = schema
        self.path = path
        self.roles = frozenset(roles)
        self.allow_introspection = allow_introspection
        self.execution_timeout = float(execution_timeout)
        self._depth_rule = _depth_validation_rule(self._max_depth)

    @staticmethod
    def _bounded_int(name: str, value: int, minimum: int, maximum: int) -> int:
        """Reject bool coercion and impractical values for resource policy fields."""
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(f"{name} must be an integer from {minimum} through {maximum}.")
        return value

    def setup(self, application: Application) -> None:
        """Contribute the GraphQL route before Core freezes application composition."""

        @application.router.route(
            self.path,
            method="POST",
            name="orbit-graphql",
            roles=self.roles,
        )
        async def graphql_endpoint(request: Request) -> Response:
            """Execute one bounded GraphQL request and return a sanitized JSON result."""
            return await self.handle(request)

    async def handle(self, request: Request) -> Response:
        """Execute a typed request while preserving Core auth, DI and cancellation behavior."""
        if len(request.body) > self._max_request_bytes:
            raise HTTPError(413, "graphql.request-too-large", "GraphQL request is too large.")
        values = self._decode_request(request)
        try:
            query = values.query
            document = parse(query, max_tokens=self._max_tokens)
        except Exception:
            return self._errors_response(("GraphQL request could not be parsed.",))
        rules = (*specified_rules, self._depth_rule)
        if not self.allow_introspection:
            rules = (*rules, NoSchemaIntrospectionCustomRule)
        try:
            errors = validate(self.schema, document, rules=rules, max_errors=self._max_errors)
        except Exception:
            return self._errors_response(("GraphQL request could not be validated.",))
        if errors:
            return self._errors_response(tuple("GraphQL request is invalid." for _ in errors))
        try:
            async with asyncio.timeout(self.execution_timeout):
                result_value = execute(
                    self.schema,
                    document,
                    context_value=request,
                    variable_values=values.variables,
                    operation_name=values.operation_name,
                )
                if hasattr(result_value, "__await__"):
                    result_value = await result_value
        except TimeoutError:
            return self._errors_response(("GraphQL execution timed out.",))
        except asyncio.CancelledError:
            raise
        except Exception:
            return self._errors_response(("GraphQL execution failed.",))
        return self._result_response(cast(ExecutionResult, result_value))

    def _decode_request(self, request: Request) -> _GraphQLRequest:
        """Decode strict JSON request fields without accepting duplicate keys or loose values."""
        headers = (
            request.headers if isinstance(request.headers, Headers) else Headers(request.headers)
        )
        content_types = headers.getall("content-type")
        if len(content_types) != 1:
            raise HTTPError(400, "graphql.content-type", "One JSON Content-Type is required.")
        media = content_types[0].partition(";")[0].strip().lower()
        if media != "application/json" and not media.endswith("+json"):
            raise HTTPError(415, "graphql.media-type", "A JSON request body is required.")
        try:
            decoded = json.loads(
                request.body.decode("utf-8"),
                object_pairs_hook=_no_duplicate_keys,
                parse_constant=_no_non_finite,
            )
            if not isinstance(decoded, Mapping):
                raise ValueError("GraphQL request must be a JSON object.")
            _validate_json_values(decoded)
            result = _GraphQLRequest.model_validate(decoded)
        except (UnicodeError, ValueError, ValidationError, RecursionError):
            raise HTTPError(
                400, "graphql.invalid-request", "Invalid GraphQL JSON request."
            ) from None
        if len(result.query) > self._max_query_characters:
            raise HTTPError(413, "graphql.query-too-large", "GraphQL query is too large.")
        return result

    def _result_response(self, result: ExecutionResult) -> Response:
        """Serialize result data while replacing resolver and coercion messages with safe text."""
        payload: dict[str, object] = {"data": result.data}
        if result.errors:
            payload["errors"] = [
                {"message": "GraphQL operation returned an error."} for _ in result.errors
            ]
        return self._json_response(payload)

    def _errors_response(self, messages: tuple[str, ...]) -> Response:
        """Format bounded public errors without echoing queries, variables, or resolver details."""
        return self._json_response({"errors": [{"message": message} for message in messages]})

    def _json_response(self, payload: Mapping[str, object]) -> Response:
        """Keep output serialization finite and below the configured response budget."""
        try:
            body = json.dumps(
                payload,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("ascii")
        except (TypeError, ValueError, UnicodeError):
            body = b'{"errors":[{"message":"GraphQL response could not be serialized."}]}'
        if len(body) > self._max_response_bytes:
            body = b'{"errors":[{"message":"Response too large."}]}'
        return Response(status=200, body=body, headers={"content-type": "application/json"})


__all__ = [
    "GraphQLPlugin",
    "MAX_GRAPHQL_DEPTH",
    "MAX_GRAPHQL_ERRORS",
    "MAX_GRAPHQL_QUERY_CHARACTERS",
    "MAX_GRAPHQL_REQUEST_BYTES",
    "MAX_GRAPHQL_RESPONSE_BYTES",
    "MAX_GRAPHQL_TOKENS",
    "MAX_GRAPHQL_TIMEOUT_SECONDS",
    "MAX_GRAPHQL_VARIABLE_VALUES",
]
