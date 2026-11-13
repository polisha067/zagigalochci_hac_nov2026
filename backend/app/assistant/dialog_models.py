from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, StrictBool

from backend.calculation_models import Hours, InputModel, NonnegativeHours, Positive, Text
from backend.network.delivery_forecast import TOOL as FORECAST_TOOL
from backend.network.models import Mode

from .schemas import ChatResponse, MessageText, ToolState


Year = Annotated[int, Field(strict=True, ge=1900, le=2199)]
Years = Annotated[list[Year], Field(min_length=2, max_length=2)]
ShipmentTool = Literal["calculate_russia", FORECAST_TOOL]


class DialogRequest(InputModel):
    message: MessageText
    turn_id: UUID
    revision: Annotated[int, Field(strict=True, ge=0)]


class CargoPropertiesChange(InputModel):
    phase: Literal["solid", "liquid", "gas"] | None = None
    hazardous: StrictBool | None = None
    handling_profile: Text | None = None
    temperature_control_required: StrictBool | None = None


class CargoChange(InputModel):
    id: Text | None = None
    name: Text | None = None
    weight_tonnes: Positive | None = None
    volume_m3: Positive | None = None
    packaging: Text | None = None
    splittable: StrictBool | None = None
    max_piece_tonnes: Positive | None = None
    max_piece_dimensions_m: tuple[Positive, Positive, Positive] | None = None
    properties: CargoPropertiesChange | None = None
    temperature_c: tuple[float, float] | None = None
    pickup_at: Text | None = None
    delivery_at: Text | None = None


class StopChange(InputModel):
    operation: Literal["add", "replace", "remove", "move", "update"]
    city: Text
    replacement: Text | None = None
    after: Text | None = None
    before: Text | None = None
    dwell_hours: NonnegativeHours | None = None
    action: Literal["visit", "pickup", "delivery", "pickup_delivery"] | None = None


class ShipmentChanges(InputModel):
    origin: Text | None = None
    destination: Text | None = None
    target_year: Year | None = None
    ready_at: datetime | None = None
    cargoes: Annotated[list[CargoChange], Field(min_length=1, max_length=20)] | None = None
    replace_cargoes: StrictBool | None = None
    stops: Annotated[list[StopChange], Field(min_length=1, max_length=16)] | None = None
    allowed_modes: Annotated[list[Mode], Field(min_length=1, max_length=6)] | None = None
    preferred_mode: Mode | None = None
    objective: Literal["cheapest", "fastest"] | None = None
    max_delivery_hours: Hours | None = None
    deliver_by: datetime | None = None
    capacity_basis: Literal["mass_only", "mass_and_volume"] | None = None
    cost_basis: Literal["including_vat", "excluding_vat"] | None = None
    mass_basis: Literal["net", "gross"] | None = None


class Evidence(InputModel):
    path: Text
    quote: MessageText


class ShipmentTurn(InputModel):
    action: Literal["new", "update", "compare", "clarify", "unsupported"]
    changes: ShipmentChanges
    evidence: list[Evidence] = Field(max_length=180)
    comparison_years: Years | None = None
    tool_hint: ShipmentTool | None = None


class DialogOptions(InputModel):
    # Keep stored restrictions from dialogs created before the stage-one parser.
    model_config = ConfigDict(extra="ignore")
    objective: Literal["cheapest", "fastest"] = "cheapest"
    preferred_mode: Mode | None = None
    deliver_by: datetime | None = None
    requirements: list[dict] = Field(default_factory=list)


class DialogSnapshot(InputModel):
    draft: ToolState | None = None
    result: ChatResponse | None = None
    result_revision: int | None = None
    options: DialogOptions = Field(default_factory=DialogOptions)
    scenarios: list["ScenarioReply"] = Field(default_factory=list, max_length=5)
    pending_remove: Text | None = None
    pending_stop: dict | None = None
    pending_year: Year | None = None
    readiness: dict = Field(default_factory=dict)


class DialogState(InputModel):
    # Old lookup and comparison state belonged to the previous dialog planner.
    model_config = ConfigDict(extra="ignore")
    current: DialogSnapshot = Field(default_factory=DialogSnapshot)
    comparison_years: Years | None = None
    previous: list[DialogSnapshot] = Field(default_factory=list, max_length=5)
    proposed_split: DialogSnapshot | None = None
    volume_forecast: ToolState | None = None


class ScenarioReply(InputModel):
    title: Text
    answer: ChatResponse


DialogSnapshot.model_rebuild()


class DialogReply(InputModel):
    revision: int
    answer: ChatResponse | None = None
    known: list[str] = Field(default_factory=list)
    changes: list[str] = Field(default_factory=list)
    scenarios: list[ScenarioReply] = Field(default_factory=list)
    # Cached replies for old turn IDs can still contain related answers.
    related: list[ChatResponse] = Field(default_factory=list)
    selected_candidate: str | None = None
    settlement_names: dict[str, str] = Field(default_factory=dict)
