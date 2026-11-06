from ..context import Ctx
from ..match import model_diff
from ..models import Example
from .common import add_example, feature_of_ts, fence, hint


def build(ctx: Ctx) -> list[Example]:
    examples: list[Example] = []
    for pair in ctx.dto_pairs:
        csharp = ctx.cs_by_name[pair.csharp]
        typescript = ctx.ts_by_name[pair.typescript]
        differences = model_diff(csharp, typescript)
        agrees = not any(differences.values())
        prompt = (
            "Convert this C# class into the TypeScript model used by the Angular client. Follow the project's model conventions."
            + hint(ctx, "Target file", typescript.path)
            + (f"\nName the TypeScript class `{typescript.name}`." if pair.matched_by == "structure" else "")
            + "\n\n"
            + fence("csharp", csharp.code)
        )
        examples.append(add_example(
            ctx,
            "dto_to_ts",
            feature_of_ts(typescript.path),
            prompt,
            fence("typescript", typescript.file_text),
            csharp=csharp.path,
            typescript=typescript.path,
            matched_by=pair.matched_by,
            rule_based_agrees=agrees,
        ))
    return examples