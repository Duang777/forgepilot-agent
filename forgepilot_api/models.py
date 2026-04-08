from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    imagePaths: list[str] | None = None


class ImageAttachment(BaseModel):
    data: str
    mimeType: str


class ModelConfig(BaseModel):
    apiKey: str | None = None
    baseUrl: str | None = None
    model: str | None = None
    apiType: Literal["anthropic-messages", "openai-completions"] | None = None


class SkillsConfig(BaseModel):
    enabled: bool = True
    userDirEnabled: bool = True
    appDirEnabled: bool = True
    skillsPath: str | None = None


class McpConfig(BaseModel):
    enabled: bool = True
    userDirEnabled: bool = True
    appDirEnabled: bool = True
    mcpConfigPath: str | None = None


class SandboxConfig(BaseModel):
    enabled: bool = True
    provider: str | None = None
    image: str | None = None
    apiEndpoint: str | None = None
    providerConfig: dict[str, Any] | None = None


class AgentRequest(BaseModel):
    prompt: str
    sessionId: str | None = None
    conversation: list[ConversationMessage] | None = None
    phase: Literal["plan", "execute"] | None = None
    planId: str | None = None
    workDir: str | None = None
    taskId: str | None = None
    modelConfig: ModelConfig | None = None
    sandboxConfig: SandboxConfig | None = None
    images: list[ImageAttachment] | None = None
    skillsConfig: SkillsConfig | None = None
    mcpConfig: McpConfig | None = None
    language: str | None = None


class ExecuteRequest(BaseModel):
    planId: str
    prompt: str = ""
    workDir: str | None = None
    taskId: str | None = None
    modelConfig: ModelConfig | None = None
    sandboxConfig: SandboxConfig | None = None
    skillsConfig: SkillsConfig | None = None
    mcpConfig: McpConfig | None = None
    language: str | None = None


class PlanStep(BaseModel):
    id: str
    description: str
    status: Literal["pending", "in_progress", "completed", "failed", "cancelled"] = "pending"


class TaskPlan(BaseModel):
    id: str
    goal: str
    steps: list[PlanStep]
    notes: str | None = None
    createdAt: str | None = None


class AgentEvent(BaseModel):
    type: str
    content: str | None = None
    name: str | None = None
    id: str | None = None
    input: Any | None = None
    subtype: str | None = None
    cost: float | None = None
    duration: int | None = None
    message: str | None = None
    sessionId: str | None = None
    toolUseId: str | None = None
    output: str | None = None
    isError: bool | None = None
    plan: TaskPlan | None = None
    permission: dict[str, Any] | None = None


class CreateTaskInput(BaseModel):
    id: str
    session_id: str
    task_index: int
    prompt: str


class CreateMessageInput(BaseModel):
    task_id: str
    type: str
    content: str | None = None
    tool_name: str | None = None
    tool_input: str | None = None
    tool_output: str | None = None
    tool_use_id: str | None = None
    subtype: str | None = None
    error_message: str | None = None
    attachments: str | None = None


class CreateFileInput(BaseModel):
    task_id: str
    name: str
    type: str
    path: str
    preview: str | None = None
    thumbnail: str | None = None


class UpdateTaskInput(BaseModel):
    status: str | None = None
    cost: float | None = None
    duration: int | None = None
    prompt: str | None = None
    favorite: bool | None = None


class TitleRequest(BaseModel):
    prompt: str = Field(min_length=1)
    modelConfig: ModelConfig | None = None
    language: str | None = None
