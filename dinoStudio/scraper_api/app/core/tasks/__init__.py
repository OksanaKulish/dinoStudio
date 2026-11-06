"""Training-example builders ported from the cleanup notebook."""

from .backend_to_frontend import build as build_backend_to_frontend
from .dto_to_ts import build as build_dto_to_ts
from .endpoint_to_method import build as build_endpoint_to_method
from .feature_scaffold import build as build_feature_scaffold
from .find_the_bug import build as build_find_the_bug
from .frontend_to_backend import build as build_frontend_to_backend
from .method_to_endpoint import build as build_method_to_endpoint
from ..checks import check_all
from ..context import Ctx
from ..models import Example

__all__ = [
    "build_backend_to_frontend",
    "build_dto_to_ts",
    "build_endpoint_to_method",
    "build_feature_scaffold",
    "build_find_the_bug",
    "build_frontend_to_backend",
    "build_method_to_endpoint",
    "build_all",
]


def build_all(ctx: Ctx) -> list[Example]:
    """Build all notebook task types in notebook order and store them on Ctx."""
    check_all(ctx)
    ctx.examples.clear()
    build_backend_to_frontend(ctx)
    build_frontend_to_backend(ctx)
    build_dto_to_ts(ctx)
    build_endpoint_to_method(ctx)
    build_method_to_endpoint(ctx)
    build_find_the_bug(ctx)
    build_feature_scaffold(ctx)
    return ctx.examples