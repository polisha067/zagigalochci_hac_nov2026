import logging
from time import perf_counter
from typing import Optional

import httpx
from pydantic import BaseModel, StrictStr

from .service import AssistantUnavailable, InvalidAssistantTurn

logger = logging.getLogger("uvicorn.error")


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
        started = perf_counter()
        try:
            response = self.http.post("/api/chat", json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AssistantUnavailable from exc
        finally:
            logger.debug("assistant model=%s elapsed_ms=%.0f", self.model, (perf_counter() - started) * 1000)

        try:
            data = response.json()
            if not isinstance(data, dict) or data.get("done") is not True or data.get("done_reason") == "length":
                raise ValueError("Ollama не завершил генерацию.")
            return OllamaMessage.model_validate(data.get("message"))
        except ValueError as exc:
            raise InvalidAssistantTurn("Некорректный ответ Ollama.") from exc
