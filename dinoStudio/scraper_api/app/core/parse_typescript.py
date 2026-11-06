import re

from .models import HttpCall, TypeScriptField, TypeScriptService, TsModel
from .text_utils import leading_block, match_brace, skip_string_literal, split_top_level

_TS_CLASS_RE = re.compile(
    r"(?m)^[ \t]*export\s+(?:abstract\s+)?(?P<kind>class|interface)\s+"
    r"(?P<name>\w+)(?P<tparams><[^>{]+>)?[^{}\n]*\{"
)
_TS_FIELD_RE = re.compile(
    r"(?m)^[ \t]*(?:(?:public\s+|readonly\s+))*"
    r"(?P<name>\w+)(?P<optional>\?)?\s*:\s*(?P<type>[^;=\n]+?)\s*(?:=[^;\n]*)?;"
)
_TS_METHOD_RE = re.compile(
    r"(?m)^[ \t]*(?:(?:public|private|protected|static|readonly|async|override|abstract)\s+)*"
    r"(?P<name>\w+)\??\s*(?:<[^>\n]+>)?\s*\("
)
_HTTP_CALL_RE = re.compile(r"this\.http\.(?P<verb>get|post|put|delete|patch)\s*(?:<[^>(]*>)?\s*\(")
_TS_NON_METHOD_NAMES = {"if", "for", "while", "switch", "catch", "return", "throw", "new"}


def normalize_url_expr(expression: str) -> tuple[str, bool]:
    """Normalize URL concatenation/template expressions as in the notebook."""
    output = ""
    dynamic_tail = False
    for index, part in enumerate(split_top_level(expression, "+")):
        if part[:1] in "'\"`":
            literal = part[1:-1]
            if part[0] == "`":
                literal = re.sub(r"\$\{[^}]*\}", "{}", literal)
            output += literal
        elif index == 0:
            continue
        elif output.endswith("/"):
            output += "{}"
        else:
            dynamic_tail = True
    output = re.sub(r"^\{\}(?=/)", "", output).split("?")[0]
    return "/" + output.strip("/"), dynamic_tail


def _find_method_terminator(text: str, start: int) -> tuple[int, str] | None:
    parentheses = 0
    brackets = 0
    index = start
    while index < len(text):
        if text.startswith("//", index):
            newline = text.find("\n", index)
            index = len(text) if newline == -1 else newline
            continue
        if text.startswith("/*", index):
            close = text.find("*/", index + 2)
            index = len(text) if close == -1 else close + 2
            continue
        string_end = skip_string_literal(text, index)
        if string_end is not None:
            index = string_end
            continue
        char = text[index]
        if char == "(":
            parentheses += 1
        elif char == ")":
            parentheses = max(0, parentheses - 1)
        elif char == "[":
            brackets += 1
        elif char == "]":
            brackets = max(0, brackets - 1)
        elif parentheses == 0 and brackets == 0:
            if char == "{":
                return index, "body"
            if char == ";":
                return index, "semicolon"
        index += 1
    return None


def _method_records(text: str, start: int, end: int) -> list[dict[str, str | int]]:
    methods: list[dict[str, str | int]] = []
    for match in _TS_METHOD_RE.finditer(text, start, end):
        method_name = match.group("name")
        if method_name in _TS_NON_METHOD_NAMES:
            continue
        params_open = match.end() - 1
        params_close = match_brace(text, params_open, "(", ")")
        if params_close <= params_open or params_close >= end:
            continue
        terminator = _find_method_terminator(text, params_close + 1)
        if terminator is None:
            continue
        body_start, body_kind = terminator
        method_end = match_brace(text, body_start) if body_kind == "body" else body_start
        return_type = text[params_close + 1:body_start].strip()
        if return_type.startswith(":"):
            return_type = return_type[1:].strip()
        method_start, _ = leading_block(text, match.start())
        methods.append({
            "name": method_name,
            "params": text[params_open + 1:params_close],
            "return_type": return_type,
            "signature_start": match.start(),
            "start": method_start,
            "end": method_end + 1,
        })
    return methods


def resolve_body(method_text: str, params: str, body_expression: str) -> tuple[str, list[str]]:
    """Resolve a named parameter or local object literal to its type and keys."""
    identifier = body_expression.strip()
    if not re.fullmatch(r"\w+", identifier):
        return "inline", []
    parameter = re.search(rf"\b{re.escape(identifier)}\s*\??\s*:\s*([\w.<>,\[\]| &]+)", params)
    if parameter is not None:
        return parameter.group(1).strip(), []
    local = re.search(
        rf"(?:const|let|var)\s+{re.escape(identifier)}\s*(?::\s*([\w.<>,\[\]| &]+))?\s*=\s*\{{",
        method_text,
    )
    if local is None:
        return "unknown", []
    literal_open = local.end() - 1
    literal_close = match_brace(method_text, literal_open)
    literal = method_text[literal_open + 1:literal_close]
    keys = [part.split(":", 1)[0].strip() for part in split_top_level(literal) if ":" in part]
    return local.group(1) or "object", keys


def parse_ts_file(text: str, path: str) -> tuple[list[TsModel], TypeScriptService | None, list[HttpCall]]:
    models: list[TsModel] = []
    service: TypeScriptService | None = None
    calls: list[HttpCall] = []

    for class_match in _TS_CLASS_RE.finditer(text):
        class_open = class_match.end() - 1
        class_end = match_brace(text, class_open)
        class_start, decorators = leading_block(text, class_match.start())
        class_name = class_match.group("name")
        class_body = text[class_open + 1:class_end]
        is_service = "this.http." in class_body
        if is_service:
            constructor = re.search(r"\bconstructor\s*\([^)]*\)", class_body)
            service = TypeScriptService(
                name=class_name,
                path=path,
                ctor=re.sub(r"\s+", " ", constructor.group(0)) if constructor else "",
            )
        elif not decorators:
            fields = [
                TypeScriptField(
                    name=field.group("name"),
                    type=field.group("type").strip(),
                    optional=bool(field.group("optional")),
                )
                for field in _TS_FIELD_RE.finditer(text, class_open + 1, class_end)
            ]
            if fields:
                models.append(TsModel(
                    name=class_name,
                    tparams=class_match.group("tparams") or "",
                    kind=class_match.group("kind"),
                    path=path,
                    fields=fields,
                    code=text[class_start:class_end + 1].strip(),
                    file_text=text.strip(),
                ))

        methods = _method_records(text, class_open + 1, class_end)
        for http_match in _HTTP_CALL_RE.finditer(text, class_open + 1, class_end):
            args_open = http_match.end() - 1
            args_close = match_brace(text, args_open, "(", ")")
            if args_close <= args_open or args_close > class_end:
                continue
            args = split_top_level(text[args_open + 1:args_close])
            method = next((
                candidate for candidate in methods
                if int(candidate["signature_start"]) <= http_match.start() < int(candidate["end"])
            ), None)
            if not args or method is None:
                continue

            verb = http_match.group("verb").upper()
            method_text = text[int(method["start"]):int(method["end"])]
            route, dynamic_tail = normalize_url_expr(args[0])
            body_expression = args[1] if verb in {"POST", "PUT", "PATCH"} and len(args) > 1 else None
            if body_expression:
                body_type, sent_keys = resolve_body(method_text, str(method["params"]), body_expression)
            else:
                body_type, sent_keys = None, []
            response_match = re.fullmatch(r"Observable<(.+)>", str(method["return_type"]))
            response_type = response_match.group(1) if response_match else str(method["return_type"] or "void")
            feature_match = re.search(r"/app/([^/]+)/", "/" + path.split("src/", 1)[-1])
            calls.append(HttpCall(
                service=service.name if service else "",
                service_path=path,
                method=str(method["name"]),
                verb=verb,
                url_expr=args[0],
                route=route.lower(),
                dynamic_tail=dynamic_tail,
                query_names=sorted(set(re.findall(r"[?&](\w+)=", method_text))),
                body_expr=body_expression,
                body_type=body_type,
                sent_keys=sent_keys,
                response_type=response_type,
                code=method_text.strip(),
                ctor=service.ctor if service else "",
                feature=feature_match.group(1) if feature_match else "",
            ))

    return models, service, calls