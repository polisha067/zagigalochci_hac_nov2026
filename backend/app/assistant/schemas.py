from datetime import date
from pathlib import PurePosixPath
from typing import Annotated, List, Literal, Optional, Union

from pydantic import Field, HttpUrl, StrictBool, StringConstraints, field_validator, model_validator

from backend.calculation_models import (
    CalculationResponse,
    Frequency,
    Hours,
    InputModel,
    Moment,
    Positive,
    Text,
)


MessageText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
ReplyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]


class ScheduleDraft(InputModel):
    mode: Optional[Literal["once", "regular"]] = None
    ready_at: Optional[Moment] = None
    start_at: Optional[Moment] = None
    end_at: Optional[Moment] = None
    frequency: Optional[Frequency] = None


class DeliveryDraft(InputModel):
    origin: Optional[Text] = None
    destination: Optional[Text] = None
    cargo_type: Optional[Text] = None
    weight_tonnes: Optional[Positive] = None
    volume_m3: Optional[Positive] = None
    splittable: Optional[StrictBool] = None
    schedule: Optional[ScheduleDraft] = None
    max_delivery_hours: Optional[Hours] = None
    capacity_basis: Optional[Literal["mass_only", "mass_and_volume"]] = None


class ChatRequest(InputModel):
    message: MessageText
    draft: Optional[DeliveryDraft] = None


class DocumentSource(InputModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    url: Optional[HttpUrl] = None
    path: Optional[Text] = None
    section: Optional[Text] = None
    date: Optional[date] = None

    @field_validator("path")
    @classmethod
    def relative_path(cls, value):
        if value is not None:
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value:
                raise ValueError("Укажите относительный путь внутри проекта")
        return value

    @model_validator(mode="after")
    def require_reference(self):
        if self.url is None and self.path is None:
            raise ValueError("Укажите ссылку или путь к документу")
        return self


class DeliveryTurn(InputModel):
    """Нормализованный черновик, предложенный обработчиком сообщения."""

    draft: DeliveryDraft


class InformationTurn(InputModel):
    reply: ReplyText
    sources: List[DocumentSource] = Field(default_factory=list, max_length=10)


class ReplyBase(InputModel):
    reply: ReplyText
    draft: Optional[DeliveryDraft]
    missing_fields: List[str] = Field(default_factory=list)
    sources: List[DocumentSource] = Field(default_factory=list)


class ClarificationReply(ReplyBase):
    kind: Literal["clarification"]
    draft: DeliveryDraft
    missing_fields: List[str] = Field(min_length=1)
    calculation: None = None


class CalculationReply(ReplyBase):
    kind: Literal["calculation"]
    draft: DeliveryDraft
    calculation: CalculationResponse


class InformationReply(ReplyBase):
    kind: Literal["information"]
    calculation: None = None


ChatResponse = Annotated[
    Union[ClarificationReply, CalculationReply, InformationReply], Field(discriminator="kind")
]
