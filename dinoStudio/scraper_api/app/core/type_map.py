import re

from .text_utils import split_top_level

_SCALAR_TYPES = {
    "string": "string",
    "char": "string",
    "guid": "string",
    "datetime": "string",
    "datetimeoffset": "string",
    "dateonly": "string",
    "timeonly": "string",
    "timespan": "string",
    "uri": "string",
    "bool": "boolean",
    "boolean": "boolean",
    "byte": "number",
    "sbyte": "number",
    "short": "number",
    "ushort": "number",
    "int": "number",
    "uint": "number",
    "long": "number",
    "ulong": "number",
    "float": "number",
    "double": "number",
    "decimal": "number",
    "object": "any",
    "dynamic": "any",
}
_LIST_TYPES = {
    "list", "ilist", "ienumerable", "icollection", "ireadonlylist",
    "ireadonlycollection", "hashset", "iset",
}
_DICTIONARY_TYPES = {"dictionary", "idictionary", "ireadonlydictionary"}


def is_nullable(csharp_type: str) -> bool:
    """Return whether a C# type uses nullable-reference/value syntax."""
    normalized = re.sub(r"\s+", "", csharp_type)
    return normalized.endswith("?") or normalized.startswith("Nullable<")


def cs_to_ts(csharp_type: str) -> str:
    """Map common C# scalar and collection types to TypeScript equivalents."""
    normalized = re.sub(r"\s+", "", csharp_type).rstrip("?")
    nullable = re.fullmatch(r"Nullable<(.+)>", normalized)
    if nullable:
        return cs_to_ts(nullable.group(1))
    if normalized == "byte[]":
        return "string"
    if normalized.endswith("[]"):
        return f"{cs_to_ts(normalized[:-2])}[]"

    generic = re.fullmatch(r"([\w.]+)<(.+)>", normalized)
    if generic:
        base_name = generic.group(1).split(".")[-1].lower()
        arguments = split_top_level(generic.group(2))
        if base_name in _LIST_TYPES and arguments:
            return f"{cs_to_ts(arguments[0])}[]"
        if base_name in _DICTIONARY_TYPES and len(arguments) == 2:
            return f"{{ [key: string]: {cs_to_ts(arguments[1])} }}"
        return f"{generic.group(1).split('.')[-1]}<{', '.join(cs_to_ts(arg) for arg in arguments)}>"

    name = normalized.split(".")[-1]
    return _SCALAR_TYPES.get(name.lower(), name)
