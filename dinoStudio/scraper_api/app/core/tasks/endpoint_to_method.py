from ..context import Ctx
from ..models import Example
from .common import add_example, csharp_context, fence


def build(ctx: Ctx) -> list[Example]:
    examples: list[Example] = []
    for match in ctx.matches:
        endpoint = ctx.endpoints[match.ep_i]
        call = ctx.calls[match.call_i]
        related_types = csharp_context(ctx, endpoint)
        prompt = (
            f"Write the Angular service method for this ASP.NET Core endpoint.\n\n"
            f"Route: {endpoint.verb} {endpoint.route}"
            + (f"\nAuth: {endpoint.auth}" if endpoint.auth else "")
            + f"\nService class: `{call.service}` ({call.ctor})\n\n"
            + "Controller:\n"
            + fence("csharp", endpoint.class_decl + "\n{ ... }")
            + "\n\nEndpoint:\n"
            + fence("csharp", endpoint.code)
            + ("\n\nRelated C# types:\n" + fence("csharp", related_types) if related_types else "")
        )
        examples.append(add_example(
            ctx,
            "endpoint_to_method",
            endpoint.feature,
            prompt,
            fence("typescript", call.code),
            endpoint=endpoint.id,
            method=f"{call.service}.{call.method}",
            confidence=match.confidence,
        ))
    return examples