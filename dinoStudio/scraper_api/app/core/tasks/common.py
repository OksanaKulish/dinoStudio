import re
import textwrap
from typing import Any

from ..context import Ctx
from ..match import type_tokens
from ..models import Endpoint, Example, HttpCall


def fence(language: str, body: str) -> str:
	return f"```{language}\n{textwrap.dedent(body).rstrip()}\n```"


def hint(ctx: Ctx, label: str, path: str) -> str:
	return f"\n{label}: `{path}`" if ctx.config.include_path_hint else ""


def feature_of_ts(path: str) -> str:
	match = re.search(r"/app/([^/]+)/", "/" + path.split("src/", 1)[-1])
	return match.group(1) if match else "shared"


def add_example(
	ctx: Ctx,
	task_type: str,
	feature: str,
	prompt: str,
	response: str,
	source: str = "real",
	**metadata: Any,
) -> Example:
	example = Example(
		task_type=task_type,
		feature=feature,
		prompt=prompt.strip(),
		response=response.strip(),
		source=source,
		metadata=metadata,
	)
	ctx.examples.append(example)
	return example


def csharp_context(ctx: Ctx, endpoint: Endpoint) -> str:
	names = [
		name for name in type_tokens(endpoint.body_type) | type_tokens(endpoint.response_type)
		if name in ctx.cs_by_name
	]
	return "\n\n".join(textwrap.dedent(ctx.cs_by_name[name].code) for name in sorted(names))


def typescript_context(ctx: Ctx, call: HttpCall) -> str:
	names = [
		name for name in type_tokens(call.body_type) | type_tokens(call.response_type)
		if name in ctx.ts_by_name
	]
	return "\n\n".join(ctx.ts_by_name[name].code for name in sorted(names))