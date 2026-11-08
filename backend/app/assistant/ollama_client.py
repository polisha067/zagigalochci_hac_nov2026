from typing import Optional

import httpx
from pydantic import BaseModel, StrictStr

from .service import AssistantUnavailable, InvalidAssistantTurn


class OllamaMessage(BaseModel):
    content: StrictStr = ""
    tool_calls: Optional[list] = None


class OllamaClient:
    def __init__(self, http: httpx.Client, model: str):
        self.http = http
        self.model = model

    def chat(self, messages: list, tools: list) -> OllamaMessage:
        try:
            response = self.http.post("/api/chat", json={
                "model": self.model,
                "messages": messages,
                "tools": tools,
                "stream": False,
                "think": False,
                "options": {"temperature": 0, "num_predict": 768},
            })
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
