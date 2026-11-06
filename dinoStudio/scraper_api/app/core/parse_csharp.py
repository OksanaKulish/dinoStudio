import re

from .models import CSharpClass, CSharpProperty, Endpoint, EndpointParameter
from .text_utils import leading_block, match_brace, skip_string_literal, split_top_level

_MODIFIERS = (
	"public", "protected", "private", "internal", "static", "abstract", "virtual", "override",
	"sealed", "partial", "async", "extern", "unsafe", "new", "readonly", "ref",
)
_MODIFIER_PATTERN = "(?:" + "|".join(_MODIFIERS) + ")"
_CS_TYPE_HEAD_RE = re.compile(
	rf"(?m)^[ \t]*(?:(?:{_MODIFIER_PATTERN})\s+)*(?P<kind>record|class|struct)"
	r"(?:\s+(?P<record_kind>class|struct))?\s+(?P<name>@?\w+)(?P<tparams><[^>{}\n]+>)?"
)
_CS_METHOD_HEAD_RE = re.compile(
	rf"(?m)^[ \t]*(?:(?:{_MODIFIER_PATTERN})\s+)*"
	r"(?P<return_type>[\w@][\w@.:<>,?\[\]\s]*?)\s+"
	r"(?P<name>@?\w+)(?:\s*<[^>\n]+>)?\s*\("
)
_CS_PROPERTY_RE = re.compile(
	r"public\s+(?P<type>[\w<>\[\],.?\s]+?)\s+(?P<name>@?\w+)\s*\{\s*get;"
)
_ATTRIBUTE_RE = re.compile(r'\[\s*(\w+)\s*(?:\(((?:"[^"]*"|[^\]"])*)\))?\s*\]')
_NON_METHOD_RETURNS = {"return", "throw", "await", "if", "for", "foreach", "while", "switch", "using", "lock"}


def _skip_comment(text: str, index: int) -> int | None:
	if text.startswith("//", index):
		newline = text.find("\n", index)
		return len(text) if newline == -1 else newline
	if text.startswith("/*", index):
		close = text.find("*/", index + 2)
		return len(text) if close == -1 else close + 2
	return None


def _find_type_terminator(text: str, start: int) -> tuple[int, str] | None:
	parentheses = 0
	brackets = 0
	angles = 0
	index = start
	while index < len(text):
		comment_end = _skip_comment(text, index)
		if comment_end is not None:
			index = comment_end
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
		elif parentheses == 0 and brackets == 0 and char == "<":
			angles += 1
		elif parentheses == 0 and brackets == 0 and char == ">" and angles:
			angles -= 1
		elif parentheses == 0 and brackets == 0 and angles == 0:
			if char == "{":
				return index, "body"
			if char == ";":
				return index, "semicolon"
		index += 1
	return None


def _find_method_terminator(text: str, start: int) -> tuple[int, str] | None:
	parentheses = 0
	brackets = 0
	angles = 0
	index = start
	while index < len(text):
		comment_end = _skip_comment(text, index)
		if comment_end is not None:
			index = comment_end
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
		elif parentheses == 0 and brackets == 0 and char == "<":
			angles += 1
		elif parentheses == 0 and brackets == 0 and char == ">" and angles:
			angles -= 1
		elif parentheses == 0 and brackets == 0 and angles == 0:
			if text.startswith("=>", index):
				return index, "expression"
			if char == "{":
				return index, "body"
			if char == ";":
				return index, "semicolon"
		index += 1
	return None


def _find_expression_end(text: str, start: int) -> int:
	parentheses = 0
	brackets = 0
	braces = 0
	index = start
	while index < len(text):
		comment_end = _skip_comment(text, index)
		if comment_end is not None:
			index = comment_end
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
		elif char == "{":
			braces += 1
		elif char == "}":
			braces = max(0, braces - 1)
		elif char == ";" and parentheses == 0 and brackets == 0 and braces == 0:
			return index
		index += 1
	return len(text) - 1


def _remove_parameter_attributes(parameter: str) -> tuple[str, list[str]]:
	result: list[str] = []
	attributes: list[str] = []
	index = 0
	while index < len(parameter):
		string_end = skip_string_literal(parameter, index)
		if string_end is not None:
			result.append(parameter[index:string_end])
			index = string_end
			continue
		if parameter[index] != "[":
			result.append(parameter[index])
			index += 1
			continue
		start = index
		depth = 1
		index += 1
		while index < len(parameter) and depth:
			string_end = skip_string_literal(parameter, index)
			if string_end is not None:
				index = string_end
				continue
			if parameter[index] == "[":
				depth += 1
			elif parameter[index] == "]":
				depth -= 1
			index += 1
		attribute = parameter[start:index]
		attributes.extend(re.findall(r"\[\s*(\w+)", attribute))
		while index < len(parameter) and parameter[index].isspace():
			index += 1
	return "".join(result).strip(), attributes


def parse_attrs(lines: list[str]) -> list[dict[str, str | None]]:
	attributes: list[dict[str, str | None]] = []
	for line in lines:
		for match in _ATTRIBUTE_RE.finditer(line):
			arguments = (match.group(2) or "").strip()
			template_match = re.search(r'"([^"\\]*(?:\\.[^"\\]*)*)"', arguments)
			template = template_match.group(1) if template_match else None
			attributes.append({"name": match.group(1), "args": arguments, "template": template})
	return attributes


def parse_cs_params(params: str) -> list[EndpointParameter]:
	parsed: list[EndpointParameter] = []
	for parameter in split_top_level(params):
		clean, attributes = _remove_parameter_attributes(parameter)
		clean = re.sub(r"^(?:(?:this|params|ref|out|in|scoped|readonly)\s+)+", "", clean)
		default: str | None = None
		if "=" in clean:
			clean, default = (part.strip() for part in clean.split("=", 1))
		parameter_type, separator, name = clean.rpartition(" ")
		if not separator:
			continue
		parsed.append(EndpointParameter(
			name=name.strip().lstrip("@"),
			type=parameter_type.strip(),
			default=default,
			source=next((attribute for attribute in attributes if attribute.startswith("From")), None),
		))
	return parsed


def unwrap_return(return_type: str) -> str | None:
	"""Unwrap Task/ValueTask/ActionResult return types as in the notebook."""
	normalized = re.sub(r"\s+", "", return_type)
	while True:
		wrapper = re.fullmatch(r"(?:Task|ValueTask|ActionResult)<(.+)>", normalized)
		if wrapper is None:
			break
		normalized = wrapper.group(1)
	if normalized in {"void", "Task", "ValueTask"}:
		return "void"
	if normalized in {"IActionResult", "ActionResult", "IResult"}:
		return None
	return normalized


def join_route(base: str | None, sub: str | None, controller: str, action: str) -> str:
	controller_name = controller.removesuffix("Controller")
	base_route = (base or "").replace("[controller]", controller_name.lower())
	sub_route = (sub or "").replace("[action]", action.lower()).replace("[controller]", controller_name.lower())
	if sub_route.startswith(("/", "~/")):
		full_route = sub_route.lstrip("~")
	else:
		full_route = "/".join(part.strip("/") for part in (base_route, sub_route) if part)
	return "/" + full_route.strip("/")


def norm_route(route: str) -> str:
	return re.sub(r"\{[^}]*\}", "{}", route).lower()


def line_no(text: str, index: int) -> int:
	return text.count("\n", 0, index) + 1


def _parse_primary_record_parameters(text: str, start: int, end: int) -> list[CSharpProperty]:
	open_paren = text.find("(", start, end)
	if open_paren == -1:
		return []
	close_paren = match_brace(text, open_paren, "(", ")")
	if close_paren >= end:
		return []
	properties: list[CSharpProperty] = []
	for parameter in parse_cs_params(text[open_paren + 1:close_paren]):
		properties.append(CSharpProperty(name=parameter.name, type=parameter.type))
	return properties


def parse_cs_file(text: str, path: str) -> tuple[list[CSharpClass], list[Endpoint]]:
	classes: list[CSharpClass] = []
	endpoints: list[Endpoint] = []
	for class_match in _CS_TYPE_HEAD_RE.finditer(text):
		header_terminator = _find_type_terminator(text, class_match.end())
		if header_terminator is None:
			continue
		terminator_idx, terminator_kind = header_terminator
		name = class_match.group("name").lstrip("@")
		type_kind = class_match.group("kind")
		if type_kind == "record" and class_match.group("record_kind"):
			type_kind = f"record {class_match.group('record_kind')}"
		type_parameters = class_match.group("tparams") or ""
		class_start, class_attribute_lines = leading_block(text, class_match.start())
		if terminator_kind == "body":
			class_end = match_brace(text, terminator_idx)
			if class_end < terminator_idx:
				continue
			declaration_end = terminator_idx
			body_start = terminator_idx + 1
			body_end = class_end
		else:
			class_end = terminator_idx
			declaration_end = terminator_idx
			body_start = body_end = terminator_idx

		declaration = text[class_start:declaration_end].strip()
		class_attributes = parse_attrs(class_attribute_lines)
		header_text = text[class_match.start():declaration_end]
		base_match = re.search(r"\s:\s*(.*?)(?:\bwhere\b|$)", header_text, re.DOTALL)
		bases = base_match.group(1).strip() if base_match else ""
		is_controller = name.endswith("Controller") or "ControllerBase" in bases
		properties = [
			CSharpProperty(name=match.group("name").lstrip("@"), type=re.sub(r"\s+", " ", match.group("type")).strip())
			for match in _CS_PROPERTY_RE.finditer(text, body_start, body_end)
		]
		if type_kind.startswith("record"):
			existing_names = {prop.name for prop in properties}
			primary_properties = _parse_primary_record_parameters(text, class_match.end(), declaration_end)
			properties.extend(prop for prop in primary_properties if prop.name not in existing_names)

		ctor_match = re.search(rf"\bpublic\s+{re.escape(name)}\s*\([^)]*\)", text[body_start:body_end])
		class_info = CSharpClass(
			name=name,
			tparams=type_parameters,
			path=path,
			is_controller=is_controller,
			kind=type_kind,
			props=properties,
			code=text[class_start:class_end + 1] if terminator_kind == "body" else text[class_start:class_end + 1],
			decl=declaration,
			ctor=re.sub(r"\s+", " ", ctor_match.group(0)) if ctor_match else "",
		)
		classes.append(class_info)
		if not is_controller or terminator_kind != "body":
			continue

		base_route = next((attribute["template"] for attribute in class_attributes if attribute["name"] == "Route"), "")
		class_auth = next((
			"AllowAnonymous" if attribute["name"] == "AllowAnonymous" else f"Authorize({attribute['args']})"
			for attribute in class_attributes if attribute["name"] in {"Authorize", "AllowAnonymous"}
		), None)
		for method_match in _CS_METHOD_HEAD_RE.finditer(text, body_start, body_end):
			return_type = re.sub(r"\s+", " ", method_match.group("return_type")).strip()
			if return_type.lower() in _NON_METHOD_RETURNS:
				continue
			method_name = method_match.group("name").lstrip("@")
			params_open = method_match.end() - 1
			params_close = match_brace(text, params_open, "(", ")")
			if params_close <= params_open or params_close >= body_end:
				continue
			method_terminator = _find_method_terminator(text, params_close + 1)
			if method_terminator is None:
				continue
			method_body_start, body_kind = method_terminator
			if body_kind == "body":
				method_end = match_brace(text, method_body_start)
			elif body_kind == "expression":
				method_end = _find_expression_end(text, method_body_start + 2)
			else:
				method_end = method_body_start
			method_start, method_attribute_lines = leading_block(text, method_match.start())
			method_attributes = parse_attrs(method_attribute_lines)
			verb_attribute = next((
				attribute for attribute in method_attributes
				if re.fullmatch(r"Http(Get|Post|Put|Delete|Patch)", attribute["name"])
			), None)
			if verb_attribute is None:
				continue
			route_attribute = next((attribute for attribute in method_attributes if attribute["name"] == "Route"), None)
			sub_route = verb_attribute["template"] if verb_attribute["template"] is not None else (
				route_attribute["template"] if route_attribute else ""
			)
			full_route = join_route(base_route, sub_route, name, method_name)
			parameters = parse_cs_params(text[params_open + 1:params_close])
			body_parameter = next((parameter for parameter in parameters if parameter.source == "FromBody"), None)
			action_auth = next((
				"AllowAnonymous" if attribute["name"] == "AllowAnonymous" else f"Authorize({attribute['args']})"
				for attribute in method_attributes if attribute["name"] in {"Authorize", "AllowAnonymous"}
			), None)
			verb = verb_attribute["name"][4:].upper()
			endpoints.append(Endpoint(
				id=f"{name}.{method_name}[{verb} {full_route}]",
				controller=name,
				controller_path=path,
				action=method_name,
				feature=name.removesuffix("Controller").lower(),
				verb=verb,
				route=full_route,
				route_norm=norm_route(full_route),
				route_params=re.findall(r"\{\*?(\w+)", full_route),
				params=parameters,
				body_type=body_parameter.type if body_parameter else None,
				query_params=[parameter.name for parameter in parameters if parameter.source == "FromQuery"],
				response_type=unwrap_return(return_type),
				auth=action_auth or class_auth,
				code=text[method_start:method_end + 1],
				start_line=line_no(text, method_start),
				end_line=line_no(text, method_end),
				class_decl=declaration,
				ctor=class_info.ctor,
				base_route=base_route or "",
			))
	return classes, endpoints