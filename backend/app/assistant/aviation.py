from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import Field, StrictBool

from backend.calculation_models import InputModel, Positive, Text
from backend.network.chukotavia import ChukotaviaData, money, normalized, volumetric_weight_kg

from .references import AIR_PATH, number
from .schemas import DocumentSource, InformationTurn, ReferenceResult


class AirTariffQuery(InputModel):
    origin: Text = Field(description="Пункт отправления")
    destination: Text = Field(description="Пункт назначения")
    flight_date: Optional[date] = Field(default=None, description="Дата вылета в местном времени, YYYY-MM-DD")
    weight_kg: Optional[Positive] = Field(default=None, description="Вес груза с упаковкой в кг")
    volume_m3: Optional[Positive] = Field(default=None, description="Объём груза в м³")
    cost_basis: Literal["including_vat", "excluding_vat"] = "including_vat"
    special_handling: StrictBool = Field(default=False, description="Явно указаны опасность, тяжеловесные места или температурный режим")


def answer_air_tariff(query: AirTariffQuery, data: ChukotaviaData) -> InformationTurn:
    snapshot_day = date.fromisoformat(data.data["observed_on"])
    tariff_day = query.flight_date or snapshot_day
    assumptions = ["Полная стоимость, приём груза и свободное место на рейсе требуют подтверждения."]
    details = {"tariff_date": tariff_day.isoformat(), "observed_on": snapshot_day.isoformat(),
               "cost_basis": query.cost_basis, "total_cost_rub": None, "booking_confirmed": False}
    if query.flight_date is None:
        assumptions.insert(0, "Дата не указана, ставка проверена на дату снимка.")
    key = (normalized(query.origin), normalized(query.destination))
    tariff = data.tariff(key, tariff_day.isoformat()) if tariff_day.year == snapshot_day.year else None
    if tariff is None:
        return InformationTurn(
            reply=f"В снимке ЧукотАВИА не нашёл опубликованной ставки для направления "
            f"{query.origin} → {query.destination} на {tariff_day:%d.%m.%Y}. " + " ".join(assumptions),
            sources=[DocumentSource(title="Снимок тарифов ЧукотАВИА", path=AIR_PATH, date=snapshot_day)],
            result=ReferenceResult(dataset_id="chukotavia-2026", status="unavailable",
                                   details=details, assumptions=assumptions),
        )

    rate = Decimal(tariff[f"rate_{query.cost_basis}_rub_per_kg"])
    vat_label = "с НДС" if query.cost_basis == "including_vat" else "без НДС"
    valid_from = date.fromisoformat(tariff["valid_from"])
    lines = [
        f"ЧукотАВИА, {tariff['origin']} → {tariff['destination']}. "
        f"На {tariff_day:%d.%m.%Y} опубликованная ставка — {number(rate)} ₽/кг {vat_label}. "
        f"Она действует с {valid_from:%d.%m.%Y} по данным снимка от {snapshot_day:%d.%m.%Y}.",
    ]
    if query.special_handling:
        assumptions.insert(0, "Для груза со специальными требованиями условия и ставку нужно уточнить у перевозчика.")
    elif query.weight_kg is not None and query.volume_m3 is not None:
        chargeable = max(Decimal(str(query.weight_kg)), volumetric_weight_kg(Decimal(str(query.volume_m3))))
        component = money(chargeable * rate)
        details.update(chargeable_weight_kg=str(chargeable), transport_component_estimate_rub=component)
        lines.append(
            f"При расчётном весе {number(round(chargeable, 2))} кг тарифная составляющая для обычного груза "
            f"составляет около {number(component)} ₽ {vat_label}."
        )
    elif query.weight_kg is not None or query.volume_m3 is not None:
        missing = "объём в м³" if query.volume_m3 is None else "вес с упаковкой в кг"
        lines.append(f"Для оценки тарифной составляющей укажите {missing}.")
    source = data.sources[tariff["source_id"]]
    return InformationTurn(
        reply="\n".join([*lines, *assumptions]),
        result=ReferenceResult(dataset_id="chukotavia-2026", status="available", rows=[tariff],
                               details=details, assumptions=assumptions),
        sources=[DocumentSource(
            title=source["title"], url=source["url"], path=AIR_PATH,
            section=f"Страница {tariff['page']}, строка {tariff['row']}", date=valid_from,
        )],
    )
