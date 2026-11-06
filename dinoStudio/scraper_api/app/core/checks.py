import re
from dataclasses import replace
from typing import Literal

from .context import Ctx
from .match import match_call, match_project, model_diff
from .models import Endpoint, HttpCall, Issue

Severity = Literal["error", "warning", "info"]


def issue(
    severity: Severity,
    rule: str,
    detail: str,
    endpoint: Endpoint | None = None,
    call: HttpCall | None = None,
    fix: tuple[str, str] | None = None,
) -> Issue:
    return Issue(
        severity=severity,
        rule=rule,
        detail=detail,
        controller=endpoint.controller if endpoint else None,
        action=endpoint.action if endpoint else None,
        service=call.service if call else None,
        method=call.method if call else None,
        fix=fix,
    )


def check_unmatched_call(call: HttpCall, endpoints: list[Endpoint]) -> list[Issue]:
    other_verbs = [
        endpoint for endpoint in endpoints
        if endpoint.verb != call.verb and match_call(replace(call, verb=endpoint.verb), [endpoint])[0] is not None
    ]
    if other_verbs:
        verbs = ", ".join(sorted({endpoint.verb for endpoint in other_verbs}))
        endpoint = other_verbs[0]
        return [issue(
            "error",
            "http_verb_mismatch",
            f"{call.service}.{call.method} uses {call.verb} {call.route}, but that route only accepts {verbs} "
            f"({endpoint.controller}.{endpoint.action}).",
            endpoint,
            call,
        )]
    return [issue(
        "error",
        "frontend_call_without_endpoint",
        f"{call.service}.{call.method} calls {call.verb} {call.route} and no controller exposes it.",
        call=call,
    )]


def check_pair(ctx: Ctx, call: HttpCall, endpoint: Endpoint) -> list[Issue]:
    findings: list[Issue] = []
    backend_queries = {query.lower(): query for query in endpoint.query_params}
    for query in call.query_names:
        backend_query = backend_queries.get(query.lower())
        if backend_query is None:
            findings.append(issue(
                "warning",
                "query_param_mismatch",
                f"Client sends ?{query}= but {endpoint.controller}.{endpoint.action} has no such query parameter.",
                endpoint,
                call,
            ))
        elif backend_query != query:
            findings.append(issue(
                "info",
                "query_param_mismatch",
                f"Client sends ?{query}= and the action declares {backend_query} (works: binding ignores case).",
                endpoint,
                call,
            ))

    if endpoint.body_type and not call.body_expr:
        findings.append(issue(
            "error",
            "body_field_mismatch",
            f"{endpoint.action} expects a {endpoint.body_type} body but {call.method} sends none.",
            endpoint,
            call,
        ))
    elif call.body_expr and not endpoint.body_type:
        findings.append(issue(
            "warning",
            "body_field_mismatch",
            f"{call.method} sends a body but {endpoint.action} has no [FromBody] parameter.",
            endpoint,
            call,
        ))
    elif endpoint.body_type and endpoint.body_type in ctx.cs_by_name:
        csharp_dto = ctx.cs_by_name[endpoint.body_type]
        typescript_model = ctx.ts_by_name.get(call.body_type or "")
        sent = call.sent_keys or ([field.name for field in typescript_model.fields] if typescript_model else [])
        expected = {prop.name.lower(): prop.name for prop in csharp_dto.props}
        if sent:
            extra = [key for key in sent if key.lower() not in expected]
            if extra:
                findings.append(issue(
                    "warning",
                    "body_field_mismatch",
                    f"{call.method} sends {extra} which {endpoint.body_type} does not have (ignored by the server).",
                    endpoint,
                    call,
                ))
            sent_lower = {key.lower() for key in sent}
            missing = [name for key, name in expected.items() if key not in sent_lower]
            if missing:
                findings.append(issue(
                    "info",
                    "body_field_mismatch",
                    f"{call.method} never sends {missing} (present in {endpoint.body_type}).",
                    endpoint,
                    call,
                ))

    backend_response = endpoint.response_type
    frontend_response = call.response_type
    if (
        backend_response not in (None, "void")
        and frontend_response not in ("void", "Response", "any")
        and re.sub(r"\s", "", backend_response) != re.sub(r"\s", "", frontend_response)
    ):
        findings.append(issue(
            "warning",
            "response_type_mismatch",
            f"{endpoint.action} returns {backend_response} but {call.method} declares {frontend_response}.",
            endpoint,
            call,
        ))
    return findings


def check_endpoint(endpoint: Endpoint) -> list[Issue]:
    parameter_names = {parameter.name.lower() for parameter in endpoint.params}
    findings: list[Issue] = []
    for route_parameter in endpoint.route_params:
        if route_parameter.lower() in parameter_names:
            continue
        literal_route = endpoint.route.replace("{" + route_parameter + "}", "anything")
        findings.append(issue(
            "warning",
            "route_param_not_in_signature",
            f'Route "{endpoint.route}" declares {{{route_parameter}}} but {endpoint.action} has no parameter named '
            f'"{route_parameter}"; {endpoint.verb} {literal_route} would match too. '
            f'If a literal segment was intended, write "{route_parameter}" without braces.',
            endpoint,
            fix=("route_literal", route_parameter),
        ))
    return findings


def check_model_pair(ctx: Ctx, csharp_name: str, typescript_name: str) -> list[Issue]:
    csharp_dto = ctx.cs_by_name[csharp_name]
    typescript_model = ctx.ts_by_name[typescript_name]
    differences = model_diff(csharp_dto, typescript_model)
    findings: list[Issue] = []
    if differences["missing_in_ts"]:
        findings.append(issue(
            "warning",
            "model_field_mismatch",
            f"TypeScript {typescript_model.name} is missing {differences['missing_in_ts']} from C# {csharp_dto.name}.",
        ))
    if differences["extra_in_ts"]:
        findings.append(issue(
            "warning",
            "model_field_mismatch",
            f"TypeScript {typescript_model.name} has {differences['extra_in_ts']} that C# {csharp_dto.name} lacks.",
        ))
    for name, csharp_type, expected_type, actual_type in differences["type_mismatch"]:
        findings.append(issue(
            "warning",
            "model_field_mismatch",
            f"{csharp_dto.name}.{name}: C# {csharp_type} should be {expected_type} in TypeScript, found {actual_type}.",
        ))
    return findings


def check_all(ctx: Ctx) -> list[Issue]:
    """Run all notebook consistency rules and store results on the context."""
    match_project(ctx)
    findings: list[Issue] = []
    matched_calls = {match.call_i for match in ctx.matches}
    matched_endpoints = {match.ep_i for match in ctx.matches}

    for call_index, call in enumerate(ctx.calls):
        if call_index not in matched_calls:
            findings.extend(check_unmatched_call(call, ctx.endpoints))
    for match in ctx.matches:
        findings.extend(check_pair(ctx, ctx.calls[match.call_i], ctx.endpoints[match.ep_i]))
    for endpoint_index, endpoint in enumerate(ctx.endpoints):
        findings.extend(check_endpoint(endpoint))
        if endpoint_index not in matched_endpoints:
            findings.append(issue(
                "info",
                "endpoint_without_frontend_call",
                f"{endpoint.verb} {endpoint.route} ({endpoint.controller}.{endpoint.action}) is not called by any Angular service.",
                endpoint,
            ))
    for pair in ctx.dto_pairs:
        findings.extend(check_model_pair(ctx, pair.csharp, pair.typescript))

    paired_dtos = {pair.csharp for pair in ctx.dto_pairs}
    for name in sorted(ctx.referenced_types & set(ctx.cs_by_name) - paired_dtos):
        findings.append(issue(
            "info",
            "dto_without_ts_model",
            f"C# {name} is used by the API but has no TypeScript model (the client uses an untyped object or never calls it).",
        ))

    ctx.issues = findings
    return findings