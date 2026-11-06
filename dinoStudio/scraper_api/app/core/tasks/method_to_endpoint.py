from ..context import Ctx
from ..models import Example
from .common import add_example, fence, typescript_context


def build(ctx: Ctx) -> list[Example]:
    examples: list[Example] = []
    for match in ctx.matches:
        endpoint = ctx.endpoints[match.ep_i]
        call = ctx.calls[match.call_i]
        related_types = typescript_context(ctx, call)
        prompt = (
            "Write the ASP.NET Core controller action that this Angular service method calls.\n\n"
            f"Service method (`{call.service}.{call.method}`):\n"
            + fence("typescript", call.code)
            + ("\n\nRelated TypeScript models:\n" + fence("typescript", related_types) if related_types else "")
            + f'\n\nController: `{endpoint.controller}` with `[Route("{endpoint.base_route}")]`'
        )
        examples.append(add_example(
            ctx,
            "method_to_endpoint",
            endpoint.feature,
            prompt,
            fence("csharp", endpoint.code),
            endpoint=endpoint.id,
            method=f"{call.service}.{call.method}",
            confidence=match.confidence,
        ))
    return examples