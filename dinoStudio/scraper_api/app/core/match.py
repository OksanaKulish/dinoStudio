import re
from typing import TypedDict

from .context import Ctx
from .models import CSharpClass, Dto, DtoPair, Endpoint, EndpointCallMatch, HttpCall, TsModel
from .type_map import cs_to_ts

_DTO_HINT = re.compile(r"(DTO|Dto|VM\b|ViewModel|Request|Response|Helper|TransportModels|ModelsView)")


class ModelDiff(TypedDict):
    missing_in_ts: list[str]
    extra_in_ts: list[str]
    type_mismatch: list[tuple[str, str, str, str]]


def type_tokens(type_name: str | None) -> set[str]:
    return set(re.findall(r"[A-Za-z_]\w*", type_name or ""))


def select_dtos(class_records: list[CSharpClass], endpoints: list[Endpoint]) -> list[Dto]:
    """Apply the notebook's reference/name/path rule to parsed C# classes."""
    referenced = set().union(*(
        type_tokens(endpoint.body_type) | type_tokens(endpoint.response_type)
        for endpoint in endpoints
    )) if endpoints else set()
    selected: list[Dto] = []
    for class_record in class_records:
        if class_record.is_controller or not class_record.props:
            continue
        if class_record.name not in referenced and not _DTO_HINT.search(f"{class_record.path} {class_record.name}"):
            continue
        selected.append(Dto(
            name=class_record.name,
            tparams=class_record.tparams,
            path=class_record.path,
            props=class_record.props,
            code=class_record.code,
            decl=class_record.decl,
            ctor=class_record.ctor,
        ))
    return selected


def match_dtos(csharp_dtos: list[Dto], typescript_models: list[TsModel]) -> list[DtoPair]:
    """Pair DTOs by case-insensitive name, then by a unique matching field set."""
    pairs: list[DtoPair] = []
    used_typescript: set[str] = set()
    used_csharp: set[str] = set()
    typescript_by_name = {model.name.lower(): model for model in typescript_models}

    for dto in csharp_dtos:
        model = typescript_by_name.get(dto.name.lower())
        if model is not None:
            pairs.append(DtoPair(dto.name, model.name, "name"))
            used_typescript.add(model.name)
            used_csharp.add(dto.name)

    remaining_typescript = [model for model in typescript_models if model.name not in used_typescript]
    for dto in csharp_dtos:
        if dto.name in used_csharp:
            continue
        wanted_fields = {prop.name.lower() for prop in dto.props}
        candidates = [
            model for model in remaining_typescript
            if wanted_fields and {field.name.lower() for field in model.fields} == wanted_fields
        ]
        if len(candidates) == 1:
            model = candidates[0]
            pairs.append(DtoPair(dto.name, model.name, "structure"))
            remaining_typescript.remove(model)
    return pairs


def match_call(call: HttpCall, endpoints: list[Endpoint]) -> tuple[int | None, str | None]:
    """Return the best same-verb endpoint with notebook segment/wildcard scoring."""
    call_route = call.route.strip("/").split("/")
    best_index: int | None = None
    best_key: tuple[int, int] | None = None
    for endpoint_index, endpoint in enumerate(endpoints):
        if endpoint.verb != call.verb:
            continue
        backend_route = endpoint.route_norm.strip("/").split("/")
        if len(backend_route) != len(call_route):
            continue
        if not all(backend == frontend or backend == "{}" for backend, frontend in zip(backend_route, call_route)):
            continue
        exact_segments = sum(backend == frontend and backend != "{}" for backend, frontend in zip(backend_route, call_route))
        wildcard_segments = sum(backend == "{}" and frontend != "{}" for backend, frontend in zip(backend_route, call_route))
        key = (exact_segments, -wildcard_segments)
        if best_key is None or key > best_key:
            best_index, best_key = endpoint_index, key
    if best_index is None:
        return None, None
    confidence = "high" if endpoints[best_index].route_norm.strip("/") == call.route.strip("/") else "medium"
    return best_index, confidence


def model_diff(dto: Dto, model: TsModel) -> ModelDiff:
    """Compare DTO/model fields and mapped types, preserving source field order."""
    csharp_fields = {prop.name.lower(): prop for prop in dto.props}
    typescript_fields = {field.name.lower(): field for field in model.fields}
    missing_in_ts = [prop.name for prop in dto.props if prop.name.lower() not in typescript_fields]
    extra_in_ts = [field.name for field in model.fields if field.name.lower() not in csharp_fields]
    type_mismatch: list[tuple[str, str, str, str]] = []
    for prop in dto.props:
        field = typescript_fields.get(prop.name.lower())
        if field is None:
            continue
        wanted = re.sub(r"\s+|\|null|\|undefined", "", cs_to_ts(prop.type))
        actual = re.sub(r"\s+|\|null|\|undefined", "", field.type)
        if wanted != actual and "any" not in {wanted, actual}:
            type_mismatch.append((prop.name, prop.type, wanted, actual))
    return {
        "missing_in_ts": missing_in_ts,
        "extra_in_ts": extra_in_ts,
        "type_mismatch": type_mismatch,
    }


def match_project(ctx: Ctx) -> None:
    """Populate DTO indexes, reference tokens, pairs, and endpoint-call matches."""
    ctx.referenced_types = set().union(*(
        type_tokens(endpoint.body_type) | type_tokens(endpoint.response_type)
        for endpoint in ctx.endpoints
    )) if ctx.endpoints else set()
    ctx.cs_dtos = select_dtos(ctx.cs_classes, ctx.endpoints)
    ctx.cs_by_name = {dto.name: dto for dto in ctx.cs_dtos}
    ctx.ts_by_name = {model.name: model for model in ctx.ts_models}
    ctx.dto_pairs = match_dtos(ctx.cs_dtos, ctx.ts_models)
    ctx.matches = []
    for call_index, call in enumerate(ctx.calls):
        endpoint_index, confidence = match_call(call, ctx.endpoints)
        if endpoint_index is not None and confidence is not None:
            ctx.matches.append(EndpointCallMatch(call_index, endpoint_index, confidence))