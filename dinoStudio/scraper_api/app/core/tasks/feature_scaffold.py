from pathlib import Path

from ..context import Ctx
from ..match import type_tokens
from ..models import Endpoint, Example
from .common import add_example, fence, feature_of_ts

_LANGUAGE_BY_EXTENSION = {".cs": "csharp", ".ts": "typescript"}


def _endpoint_line(endpoint: Endpoint) -> str:
    details: list[str] = []
    if endpoint.query_params:
        details.append("query: " + ", ".join(endpoint.query_params))
    if endpoint.body_type:
        details.append("body: " + endpoint.body_type)
    if endpoint.auth:
        details.append("auth: " + endpoint.auth)
    line = f"- {endpoint.verb} {endpoint.route} -> {endpoint.response_type or 'IActionResult'}"
    return line + (f" ({'; '.join(details)})" if details else "")


def build(ctx: Ctx) -> list[Example]:
    examples: list[Example] = []
    features = sorted({endpoint.feature for endpoint in ctx.endpoints})
    for feature in features:
        endpoints = [endpoint for endpoint in ctx.endpoints if endpoint.feature == feature]
        controller_paths = sorted({endpoint.controller_path for endpoint in endpoints})
        service_paths = sorted({
            ctx.calls[match.call_i].service_path
            for match in ctx.matches
            if ctx.endpoints[match.ep_i].feature == feature
        })
        if not service_paths:
            continue

        csharp_paths = {
            ctx.cs_by_name[pair.csharp].path
            for pair in ctx.dto_pairs
            if feature_of_ts(ctx.ts_by_name[pair.typescript].path) == feature
        }
        referenced_paths = {
            ctx.cs_by_name[name].path
            for endpoint in endpoints
            for name in type_tokens(endpoint.body_type) | type_tokens(endpoint.response_type)
            if name in ctx.cs_by_name
        }
        csharp_paths.update(referenced_paths)
        ordered_paths = controller_paths + sorted(csharp_paths) + service_paths + sorted({
            model.path for model in ctx.ts_models if feature_of_ts(model.path) == feature
        })

        specification = (
            f'Add the "{feature}" feature to the project.\n\nAPI:\n'
            + "\n".join(_endpoint_line(endpoint) for endpoint in endpoints)
            + "\n\nProduce the controller, the C# DTOs, the Angular service and the TypeScript models."
        )
        response = "\n\n".join(
            f"### FILE: {path}\n" + fence(_LANGUAGE_BY_EXTENSION[Path(path).suffix], ctx.texts[path])
            for path in ordered_paths
        )
        examples.append(add_example(ctx, "feature_scaffold", feature, specification, response, files=ordered_paths))
    return examples