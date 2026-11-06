from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..core.config import ScraperConfig


class ScraperConfigPayload(BaseModel):
	model_config = ConfigDict(extra="forbid")

	ignore_dirs: set[str] = Field(default_factory=lambda: set(ScraperConfig().ignore_dirs))
	ignore_file_patterns: list[str] = Field(default_factory=lambda: list(ScraperConfig().ignore_file_patterns))
	allowed_extensions: set[str] = Field(default_factory=lambda: set(ScraperConfig().allowed_extensions))
	max_file_bytes: int = Field(default=200_000, ge=1, le=10_000_000)
	test_features: set[str] = Field(default_factory=lambda: set(ScraperConfig().test_features))
	max_tokens: int = Field(default=2048, ge=1, le=100_000)
	tokenizer_model: str = Field(default="Qwen/Qwen2.5-Coder-0.5B-Instruct", min_length=1, max_length=200)
	mutation_kinds: list[str] = Field(default_factory=lambda: list(ScraperConfig().mutation_kinds))
	include_path_hint: bool = True
	use_git: bool = True
	system_msg: str = Field(default=ScraperConfig().system_msg, max_length=10_000)
	max_mutations: int = Field(default=10_000, ge=0, le=100_000)

	def to_core(self) -> ScraperConfig:
		return ScraperConfig(
			ignore_dirs=frozenset(self.ignore_dirs),
			ignore_file_patterns=tuple(self.ignore_file_patterns),
			allowed_extensions=frozenset(extension.lower() for extension in self.allowed_extensions),
			max_file_bytes=self.max_file_bytes,
			test_features=frozenset(self.test_features),
			max_tokens=self.max_tokens,
			tokenizer_model=self.tokenizer_model,
			mutation_kinds=tuple(self.mutation_kinds),
			include_path_hint=self.include_path_hint,
			use_git=self.use_git,
			system_msg=self.system_msg,
			max_mutations=self.max_mutations,
		)


class LocalProjectRequest(BaseModel):
	model_config = ConfigDict(extra="forbid")
	path: str = Field(min_length=1, max_length=4096)


class ProjectResponse(BaseModel):
	project_id: str
	name: str


class StartJobRequest(BaseModel):
	model_config = ConfigDict(extra="forbid")
	project_id: str = Field(min_length=1, max_length=64)
	config: ScraperConfigPayload = Field(default_factory=ScraperConfigPayload)


class JobCreatedResponse(BaseModel):
	job_id: str


class JobEventResponse(BaseModel):
	id: int
	time: str
	message: str


class UploadStatusResponse(BaseModel):
	upload_id: str
	stage: str
	processed_files: int = 0
	total_files: int = 0
	error: str | None = None
	events: list[JobEventResponse]


class JobStatusResponse(BaseModel):
	job_id: str
	project_id: str
	project_name: str | None = None
	status: Literal["queued", "running", "completed", "failed"]
	stage: str
	percent: int = Field(ge=0, le=100)
	results_ready: bool
	events: list[JobEventResponse]
	error: str | None = None
	created_at: str
	updated_at: str


class SectionPageResponse(BaseModel):
	section: str
	items: list[dict[str, Any]]
	total: int = Field(ge=0)
	offset: int = Field(ge=0)
	limit: int = Field(ge=1)


class DefaultConfigResponse(ScraperConfigPayload):
	pass
