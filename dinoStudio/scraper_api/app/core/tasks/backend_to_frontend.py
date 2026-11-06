from collections import defaultdict

from ..context import Ctx
from ..models import Example
from .common import add_example, fence, hint


def build(ctx: Ctx) -> list[Example]:
    controller_services: dict[str, set[str]] = defaultdict(set)
    service_controllers: dict[str, set[str]] = defaultdict(set)
    controller_features: dict[str, str] = {}
    for match in ctx.matches:
        endpoint = ctx.endpoints[match.ep_i]
        call = ctx.calls[match.call_i]
        controller_services[endpoint.controller_path].add(call.service_path)
        service_controllers[call.service_path].add(endpoint.controller_path)
        controller_features[endpoint.controller_path] = endpoint.feature

    examples: list[Example] = []
    file_pairs = [
        (controller_path, next(iter(service_paths)))
        for controller_path, service_paths in controller_services.items()
        if len(service_paths) == 1 and len(service_controllers[next(iter(service_paths))]) == 1
    ]
    for controller_path, service_path in file_pairs:
        feature = controller_features[controller_path]
        examples.append(add_example(
            ctx,
            "backend_to_frontend",
            feature,
            "Write the Angular service for this controller."
            + hint(ctx, "Target file", service_path)
            + "\n\n"
            + fence("csharp", ctx.texts[controller_path]),
            fence("typescript", ctx.texts[service_path]),
            controller=controller_path,
            service=service_path,
        ))
    return examples