from typing import Optional

import httpx
from pydantic import BaseModel, StrictStr

from .service import AssistantUnavailable, InvalidAssistantTurn


class OllamaMessage(BaseModel):
    content: StrictStr = ""
    tool_calls: Optional[list] = None


class OllamaClient:
    def __init__(self, http: httpx.Client, model: str, *, num_ctx: int = 8192, num_gpu: int | None = None):
        self.http = http
        self.model = model
        self.options = {"temperature": 0, "num_ctx": num_ctx, "num_predict": 2048}
        if num_gpu is not None:
            self.options["num_gpu"] = num_gpu

    def chat(self, messages: list, tools: list, *, response_schema: Optional[dict] = None) -> OllamaMessage:
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "think": False,
            "options": self.options,
        }
        if response_schema is not None:
            payload["format"] = response_schema
        try:
            response = self.http.post("/api/chat", json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AssistantUnavailable from exc

        try:
            data = response.json()
            if not isinstance(data, dict) or data.get("done") is not True or data.get("done_reason") == "length":
                raise ValueError("Incomplete Ollama response")
            return OllamaMessage.model_validate(data.get("message"))
        except ValueError as exc:
            raise InvalidAssistantTurn from exc
