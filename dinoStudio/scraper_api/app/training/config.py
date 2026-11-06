from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TrainingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_model: str = Field(default="unsloth/Qwen2.5-Coder-7B-Instruct-bnb-4bit", min_length=1, max_length=200)
    dry_run_model: str = Field(default="Qwen/Qwen2.5-Coder-0.5B-Instruct", min_length=1, max_length=200)
    max_seq_length: int = Field(default=2048, ge=256, le=32768)
    lora_r: int = Field(default=8, ge=1, le=256)
    lora_alpha: int = Field(default=16, ge=1, le=512)
    epochs: float = Field(default=3, gt=0, le=100)
    learning_rate: float = Field(default=1e-4, gt=0, le=1)
    batch_size: int = Field(default=1, ge=1, le=128)
    grad_accum: int = Field(default=8, ge=1, le=256)
    seed: int = Field(default=42, ge=0)
    quantization: Literal["q4_k_m", "q5_k_m", "q8_0"] = "q4_k_m"
    task_types: list[str] = Field(default_factory=list)
    dry_run: bool = False

    @field_validator("task_types")
    @classmethod
    def unique_task_types(cls, value: list[str]) -> list[str]:
        if any(not task or not task.strip() for task in value):
            raise ValueError("Task types cannot be blank.")
        if len(set(value)) != len(value):
            raise ValueError("Task types cannot be repeated.")
        return value