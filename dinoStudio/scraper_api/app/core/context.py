from dataclasses import dataclass, field
from pathlib import Path

from .config import ScraperConfig
from .models import (
    Chunk,
    CSharpClass,
    Dto,
    DtoPair,
    Endpoint,
    EndpointCallMatch,
    Example,
    GitCommit,
    HttpCall,
    Issue,
    TypeScriptService,
    TsModel,
)


@dataclass(slots=True)
class Ctx:
    root: Path
    config: ScraperConfig
    files: list[Path] = field(default_factory=list)
    texts: dict[str, str] = field(default_factory=dict)
    output_dir: Path | None = None
    repo_top: Path | None = None
    git_sha: str | None = None
    history: dict[str, list[GitCommit]] = field(default_factory=dict)
    cs_classes: list[CSharpClass] = field(default_factory=list)
    cs_dtos: list[Dto] = field(default_factory=list)
    cs_by_name: dict[str, Dto] = field(default_factory=dict)
    endpoints: list[Endpoint] = field(default_factory=list)
    ts_models: list[TsModel] = field(default_factory=list)
    ts_by_name: dict[str, TsModel] = field(default_factory=dict)
    ts_services: list[TypeScriptService] = field(default_factory=list)
    calls: list[HttpCall] = field(default_factory=list)
    referenced_types: set[str] = field(default_factory=set)
    dto_pairs: list[DtoPair] = field(default_factory=list)
    matches: list[EndpointCallMatch] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    examples: list[Example] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)
    coverage: dict[str, list[str]] = field(default_factory=dict)