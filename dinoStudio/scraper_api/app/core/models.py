from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class EndpointParameter:
    name: str
    type: str
    default: str | None = None
    source: str | None = None


@dataclass(frozen=True, slots=True)
class CSharpProperty:
    name: str
    type: str


@dataclass(frozen=True, slots=True)
class TypeScriptField:
    name: str
    type: str
    optional: bool = False


@dataclass(frozen=True, slots=True)
class GitCommit:
    short_hash: str
    author: str
    iso_date: str
    subject: str


@dataclass(frozen=True, slots=True)
class DtoPair:
    csharp: str
    typescript: str
    matched_by: Literal["name", "structure"]


@dataclass(frozen=True, slots=True)
class EndpointCallMatch:
    call_i: int
    ep_i: int
    confidence: Literal["high", "medium"]


@dataclass(frozen=True, slots=True)
class Endpoint:
    id: str
    controller: str
    controller_path: str
    action: str
    feature: str
    verb: str
    route: str
    route_norm: str
    route_params: list[str] = field(default_factory=list)
    params: list[EndpointParameter] = field(default_factory=list)
    body_type: str | None = None
    query_params: list[str] = field(default_factory=list)
    response_type: str | None = None
    auth: str | None = None
    code: str = ""
    start_line: int = 1
    end_line: int = 1
    class_decl: str = ""
    ctor: str = ""
    base_route: str = ""


@dataclass(frozen=True, slots=True)
class HttpCall:
    service: str
    service_path: str
    method: str
    verb: str
    url_expr: str
    route: str
    dynamic_tail: bool = False
    query_names: list[str] = field(default_factory=list)
    body_expr: str | None = None
    body_type: str | None = None
    sent_keys: list[str] = field(default_factory=list)
    response_type: str = "void"
    code: str = ""
    ctor: str = ""
    feature: str = "shared"


@dataclass(frozen=True, slots=True)
class Dto:
    name: str
    tparams: str
    path: str
    props: list[CSharpProperty] = field(default_factory=list)
    code: str = ""
    decl: str = ""
    ctor: str = ""


@dataclass(frozen=True, slots=True)
class TypeScriptService:
    name: str
    path: str
    ctor: str = ""


@dataclass(frozen=True, slots=True)
class TsModel:
    name: str
    tparams: str
    kind: Literal["class", "interface"]
    path: str
    fields: list[TypeScriptField] = field(default_factory=list)
    code: str = ""
    file_text: str = ""


@dataclass(frozen=True, slots=True)
class Issue:
    severity: Literal["error", "warning", "info"]
    rule: str
    detail: str
    controller: str | None = None
    action: str | None = None
    service: str | None = None
    method: str | None = None
    fix: tuple[str, str] | None = None


@dataclass(slots=True)
class Example:
    task_type: str
    feature: str
    prompt: str
    response: str
    source: str = "real"
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = ""
    n_tokens: int = 0
    too_long: bool = False
    split: Literal["train", "test"] | None = None


@dataclass(frozen=True, slots=True)
class Chunk:
    id: str
    text: str
    kind: str
    feature: str
    path: str
    lang: str
    hash: str


@dataclass(frozen=True, slots=True)
class FileRecord:
    path: Path
    extension: str
    kind: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class FileCommitHistory:
    commits: list[GitCommit] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)
    last_commit_date: str | None = None
    last_commit_msg: str | None = None


@dataclass(frozen=True, slots=True)
class Manifest:
    git_sha: str | None
    config_hash: str
    counts_per_task: dict[str, int]
    tool_version: str
    timestamp: datetime


@dataclass(slots=True)
class CSharpClass:
    name: str
    tparams: str
    path: str
    is_controller: bool
    kind: str = "class"
    props: list[CSharpProperty] = field(default_factory=list)
    code: str = ""
    decl: str = ""
    ctor: str = ""