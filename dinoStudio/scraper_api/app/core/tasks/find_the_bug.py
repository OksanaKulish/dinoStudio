import re

from ..checks import check_endpoint
from ..context import Ctx
from ..match import match_call, model_diff
from ..models import Endpoint, Example, HttpCall
from ..parse_csharp import parse_cs_file
from ..parse_typescript import parse_ts_file
from ..type_map import cs_to_ts
from .common import add_example, feature_of_ts, fence

_BUG_PROMPT = (
    "The Angular service method below calls the ASP.NET Core endpoint below, but the two do not match. "
    "Find the problem and give the corrected code."
)
_VERB_SWAP = {"get": "post", "post": "put", "put": "post", "delete": "get"}


def _mutate_url(code: str) -> str | None:
    mutated, count = re.subn(r"(/api/)([A-Za-z][\w-]*)", lambda match: match.group(1) + match.group(2)[:-1], code, count=1)
    return mutated if count else None


def _mutate_verb(code: str) -> str | None:
    match = re.search(r"this\.http\.(get|post|put|delete)\b", code)
    if match is None:
        return None
    old_verb = match.group(1)
    return code[:match.start(1)] + _VERB_SWAP[old_verb] + code[match.end(1):]


def _first_call(code: str) -> HttpCall | None:
    wrapped = f"export class X {{\n{code}\n}}\n"
    _, _, calls = parse_ts_file(wrapped, "mutation.ts")
    return calls[0] if calls else None


def _call_mutation_is_detected(
    mutated_call: HttpCall,
    endpoint_index: int,
    endpoints: list[Endpoint],
) -> bool:
    matched_index, _ = match_call(mutated_call, endpoints)
    return matched_index is None or matched_index != endpoint_index


def build(ctx: Ctx) -> list[Example]:
    examples: list[Example] = []
    for finding in ctx.issues:
        if finding.rule != "route_param_not_in_signature":
            continue
        endpoint = next((
            candidate for candidate in ctx.endpoints
            if candidate.controller == finding.controller and candidate.action == finding.action
        ), None)
        if endpoint is None or finding.fix is None:
            continue
        parameter = finding.fix[1]
        fixed_code = endpoint.code.replace("{" + parameter + "}", parameter, 1)
        source_text = ctx.texts.get(endpoint.controller_path)
        if source_text is None:
            continue
        fixed_text = source_text.replace(endpoint.code, fixed_code, 1)
        _, reparsed_endpoints = parse_cs_file(fixed_text, endpoint.controller_path)
        fixed_endpoint = next((
            candidate for candidate in reparsed_endpoints
            if candidate.action == endpoint.action and candidate.verb == endpoint.verb
        ), None)
        partner_match = next((match for match in ctx.matches if ctx.endpoints[match.ep_i].id == endpoint.id), None)
        partner_call = ctx.calls[partner_match.call_i] if partner_match is not None else None
        verified = bool(fixed_endpoint) and not check_endpoint(fixed_endpoint)
        if verified and partner_call is not None:
            verified = match_call(partner_call, [fixed_endpoint]) == (0, "high")
        if not verified or fixed_endpoint is None:
            continue
        examples.append(add_example(
            ctx,
            "find_the_bug",
            endpoint.feature,
            "This ASP.NET Core endpoint is called by the Angular client. Review its route and point out any problem.\n\n"
            + fence("csharp", endpoint.code),
            finding.detail + "\n\nCorrected endpoint:\n" + fence("csharp", fixed_code),
            source="real_finding",
            verified=True,
            rule=finding.rule,
            endpoint=endpoint.id,
        ))

    mutation_count = 0
    for match in ctx.matches:
        endpoint = ctx.endpoints[match.ep_i]
        call = ctx.calls[match.call_i]
        for mutation_kind, mutate in (("url_typo", _mutate_url), ("wrong_verb", _mutate_verb)):
            if mutation_kind not in ctx.config.mutation_kinds or mutation_count >= ctx.config.max_mutations:
                continue
            bad_code = mutate(call.code)
            mutated_call = _first_call(bad_code) if bad_code else None
            if mutated_call is None or not _call_mutation_is_detected(mutated_call, match.ep_i, ctx.endpoints):
                continue
            if mutation_kind == "url_typo":
                explanation = (
                    f"The service calls `{mutated_call.url_expr}`, but {endpoint.controller}.{endpoint.action} is "
                    f"`{endpoint.verb} {endpoint.route}`. The original call used `{call.url_expr}`; the controller segment is misspelled."
                )
            else:
                explanation = (
                    f"{endpoint.controller}.{endpoint.action} accepts `{endpoint.verb}` but the method calls "
                    f"`this.http.{mutated_call.verb.lower()}`. Use `this.http.{call.verb.lower()}`."
                )
            examples.append(add_example(
                ctx,
                "find_the_bug",
                endpoint.feature,
                _BUG_PROMPT + "\n\nEndpoint:\n" + fence("csharp", endpoint.code)
                + "\n\nService method:\n" + fence("typescript", bad_code),
                explanation + "\n\nCorrected method:\n" + fence("typescript", call.code),
                source="mutation",
                verified=True,
                rule=mutation_kind,
                endpoint=endpoint.id,
                method=f"{call.service}.{call.method}",
            ))
            mutation_count += 1

    for pair in ctx.dto_pairs:
        if "drop_field" not in ctx.config.mutation_kinds or mutation_count >= ctx.config.max_mutations:
            break
        csharp = ctx.cs_by_name[pair.csharp]
        typescript = ctx.ts_by_name[pair.typescript]
        if len(csharp.props) < 3:
            continue
        candidates = [prop.name for prop in csharp.props if prop.name.lower() != "id"]
        if not candidates:
            continue
        victim = candidates[-1]
        pattern = rf"(?m)^[ \t]*{re.escape(victim)}\??\s*:[^\n]*\n"
        bad_text, replacements = re.subn(pattern, "", typescript.file_text + "\n", count=1)
        if not replacements:
            continue
        bad_models, _, _ = parse_ts_file(bad_text.strip(), typescript.path)
        bad_model = next((model for model in bad_models if model.name == typescript.name), None)
        if bad_model is None or victim not in model_diff(csharp, bad_model)["missing_in_ts"]:
            continue
        typescript_type = cs_to_ts(next(prop.type for prop in csharp.props if prop.name == victim))
        examples.append(add_example(
            ctx,
            "find_the_bug",
            feature_of_ts(typescript.path),
            "This TypeScript model is supposed to mirror the C# class that the API serialises. Find the mismatch and give the corrected model.\n\n"
            "C# class:\n" + fence("csharp", csharp.code) + "\n\nTypeScript model:\n" + fence("typescript", bad_text.strip()),
            f"`{victim}` exists on C# `{csharp.name}` but is missing from the TypeScript `{typescript.name}`, so the client never sees it. "
            f"Add `{victim}: {typescript_type};`.\n\nCorrected model:\n" + fence("typescript", typescript.file_text),
            source="mutation",
            verified=True,
            rule="drop_field",
            csharp=csharp.path,
            typescript=typescript.path,
        ))
        mutation_count += 1
    return examples