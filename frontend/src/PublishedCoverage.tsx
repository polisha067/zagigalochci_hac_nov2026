import { hours, modeNames, money, num } from "./model";
import type { AirMarketLeg, Calculation, LandMarketLeg } from "./networkApi";

const roadStatus: Record<string, string> = {
  router_unavailable: "Грузовой дорожный граф недоступен.",
  missing_coordinates: "Для одного из пунктов отсутствуют координаты.",
  path_not_found: "Грузовой путь не найден в подключённом графе.",
  ferry_required: "Найденный путь включает паром. Нужна проверка переправы.",
};

const marketVat: Record<string, string> = {
  included: "с НДС", excluded: "без НДС", unknown: "НДС не указан",
  mixed: "часть ставок без разбивки НДС",
};

const railScopes: Record<string, string> = {
  door_to_door: "от двери до двери по условиям прайса",
  warehouse_to_station: "от склада до станции",
  warehouse_to_warehouse: "от склада до склада по условиям прайса",
  station_to_station: "от станции до станции",
};

function AirLeg({ leg }: { leg: AirMarketLeg }) {
  return <p>
    {leg.origin} ({leg.departure}) → {leg.destination}{leg.arrival && ` (${leg.arrival})`}
    {!!leg.via.length && ` · через ${leg.via.join(", ")} по тарифу перевозчика`}.
    {" "}Платный вес {num(leg.paid_kg, 2)} кг. Прогноз <b>{money(leg.forecast_rub)}</b> ({marketVat[leg.vat]}).
    {leg.shipment_count > 1 && ` Сценарий: ${leg.shipment_count} отправок по ${num(leg.volume_m3_per_shipment, 2)} м³
      и ${num(leg.paid_kg_per_shipment, 2)} кг платного веса. Укладку и доступность подтвердит перевозчик.`}
    {" "}База {leg.base_year} года · {money(leg.base_rate_rub_per_kg)}/кг
    {leg.variable_fees_rub_per_kg > 0 && ` + ${money(leg.variable_fees_rub_per_kg)}/кг сборы`}
    {leg.fixed_rub > 0 && ` + ${money(leg.fixed_rub)} за отправку`} · {money(leg.base_rub)}.
    {" "}<a href={leg.source.url}>{leg.source.name}</a> · {leg.source.base_date}
    {leg.source.valid_until && ` · дата окончания ${leg.source.valid_until}`}
    {leg.source.rules_url && <>{" · "}<a href={leg.source.rules_url}>Правила расчёта</a></>}
    {leg.source.fuel_url && <>{" · "}<a href={leg.source.fuel_url}>Топливный сбор</a></>}
  </p>;
}

export function AirMarketOptions({ result, compact = false, routeIndex }: { result: Calculation; compact?: boolean; routeIndex?: number }) {
  const market = result.air_market;
  if (!market) return null;
  return <section className="notice">
    <h2>Оценка авиаперевозки · {market.target_year}</h2>
    {market.routes.length ? <>
      <p>Цена между аэропортами по опубликованным ставкам. Подвоз, довоз, дополнительные сборы,
        доступность мест и габариты упаковки уточняет перевозчик.</p>
      {market.routes.slice(0, compact ? 2 : undefined).map((option, i) => (routeIndex == null || routeIndex === i) && <div className="source-item" id={compact ? undefined : `market-air-${i}`} key={i}>
        <b>{option.transfer_count ? "Сумма отдельных авиаплеч" : "Оценка по тарифу перевозчика"}
          {" · "}{money(option.forecast_rub)} ({marketVat[option.vat]})</b>
        {option.legs.map((leg, j) => <AirLeg key={j} leg={leg} />)}
        {!!option.transfer_count && <p>Перегрузку и стыковку рейсов нужно согласовать отдельно.</p>}
      </div>)}
    </> : market.segments.some(Boolean) ?
      market.segments.map((leg, i) => leg ? <AirLeg key={i} leg={leg} /> :
        !market.weight_limits?.[i] && <p key={i}>Для авиаплеча {i + 1} нужна котировка.</p>) :
      !market.weight_limits?.some(Boolean) &&
        <p>Для этих пунктов и условий в загруженных прайсах нет применимой ставки. Для отправки нужна котировка.</p>}
    {!market.routes.length && market.weight_limits?.map((limit, i) => limit && <p key={i}>
      Авиаплечо {i + 1} · платный вес всей партии {num(limit.paid_kg, 2)} кг.
      Для расчёта одной отправки по общей ставке нужен платный вес менее {num(limit.threshold_kg, 0)} кг.
      {" "}<a href={limit.source.url}>{limit.source.name}</a> · запросите котировку на всю партию.
    </p>)}
  </section>;
}

function MarketLeg({ leg }: { leg: LandMarketLeg }) {
  const sources = leg.sources.slice(0, 3);
  return <p>
    {leg.origin} → {leg.destination} · {leg.equipment} × {leg.units} · <b>{money(leg.forecast_rub)}</b>{" "}
    ({marketVat[leg.vat]}) · {leg.kind === "model" ? "модельная ставка" : "прогноз по опубликованной ставке"}.
    {leg.scope && railScopes[leg.scope] && ` Состав услуги: ${railScopes[leg.scope]}.`}
    {(leg.origin_station || leg.destination_station) &&
      ` Станции: ${leg.origin_station ?? "по согласованию"} → ${leg.destination_station ?? "по согласованию"}.`}
    {leg.confidence === "exploratory" && " Ограниченная выборка для проверки цены."}
    {leg.distance_source === "observation" && " Расстояние из наблюдений о связности."}
    {leg.distance_source === "truck_graph" && " Расстояние по грузовому графу."}
    {leg.distance_source === "market_direct_distance" && " Справочный километраж для данного направления."}
    {leg.distance_source === "market_distance_chain" && " Километраж оценён по цепочке рыночных направлений. Проезд и срок требуют проверки."}
    {leg.distance_source === "regional_carrier_reference" && " Километраж взят из справочного расчёта перевозчика."}
    {sources.map((source, i) => <span key={source.url}>{" "}
      <a href={source.url} title={source.name}>{sources.length > 1 ? `Прайс ${i + 1}` : source.name}</a>
    </span>)}
    {leg.service && <>{" "}<a href={leg.service.source.url}>Грузовой сервис</a>
      {leg.service.days != null && ` · ориентир оператора ${leg.service.days} сут.`}</>}
  </p>;
}

export function LandMarketOptions({ result, compact = false, routeIndex, inReport = false }: {
  result: Calculation; compact?: boolean; routeIndex?: number; inReport?: boolean;
}) {
  const market = result.land_market;
  if (!market) return null;
  if (!market.unpriced_rail?.length && result.road_market_estimate && market.routes.length === 1 && market.routes[0].legs.length === 1 &&
      market.routes[0].legs[0].mode === "road" &&
      market.routes[0].legs[0].forecast_rub === result.road_market_estimate.forecast_road_rub) return null;
  return <section className={inReport ? undefined : "notice"}>
    {inReport ? <h3>Стоимость по этапам · {market.target_year}</h3>
      : <h2>Оценка транспортных плеч · {market.target_year}</h2>}
    <p>Подвоз, довоз и терминальные операции учитываются только в пределах состава исходной ставки.
      Доступность оборудования и сквозной рейс уточняются у перевозчика.</p>
    {market.routes.length ? market.routes.slice(0, compact ? (market.routes.some(row => row.scenario_note) ? 4 : 3) : undefined).map((option, i) => (routeIndex == null || routeIndex === i) &&
      <div className="source-item" id={compact ? undefined : `market-land-${i}`} key={i}>
        <b>{market.version.startsWith("reefer-market") ? `Рефрижераторы × ${option.legs[0].units}`
          : option.legs.map(leg => modeNames[leg.mode]).join(" → ")}
          {option.movement_rub != null && option.vat && ` · ${money(option.movement_rub)} (${marketVat[option.vat]})`}</b>
        {option.legs.map((leg, j) => <MarketLeg key={j} leg={leg} />)}
        {option.scenario_note ? <p>{option.scenario_note}</p> : option.rail_transfer ? <p>Передача контейнера в узле <a href={option.rail_transfer_url}>{option.rail_transfer}</a>. Терминалы, сроки
          и стоимость стыковки нужно подтвердить отдельно.</p> :
          option.transfer_count > 0 && <p>Стоимость и условия передачи груза между видами транспорта требуют подтверждения.</p>}
      </div>
    ) : market.legs.map((legs, i) => <div key={i}>
      {legs.length ? legs.map((leg, j) => <MarketLeg key={j} leg={leg} />)
        : <p>Плечо {i + 1} · нужна котировка.</p>}
    </div>)}
    {market.unpriced_rail?.map(row => <p key={row.origin + ":" + row.destination}>
      {row.origin} → {row.destination} · <a href={row.source.url}>Контейнерный ж/д сервис</a>.
      {row.days != null && ` Ориентир оператора ${row.days} сут.`}
      {" "}Для этого груза нужна котировка ж/д оператора.
    </p>)}
    {!!market.terminal_references?.length && <details>
      <summary>Ориентиры по приёму и вывозу контейнера</summary>
      <p>Ставки за контейнер применимы при прибытии на указанную станцию.
        В смету их можно включить после проверки станции и состава ж/д тарифа.</p>
      {market.terminal_references.map(ref => <p key={`${ref.station}:${ref.destination}:${ref.size_ft}`}>
        {ref.station} → {ref.destination} · {ref.size_ft} фут. · приём {money(ref.receive_rub)},
        {" "}контейнеровоз {money(ref.outbound_rub)} ({marketVat[ref.vat]}).
        {" "}<a href={ref.source.url}>{ref.source.name}</a>
      </p>)}
    </details>}
  </section>;
}

export function RoadMarketEstimate({ result, anchor }: { result: Calculation; anchor?: string }) {
  const estimate = result.road_market_estimate;
  if (!estimate) return null;
  return (
    <section className="notice" id={anchor}>
      <h2>Сценарная оценка автоплеча</h2>
      <p>{estimate.origin} → {estimate.destination} · {estimate.target_year} год ·{" "}
        <b>{money(estimate.forecast_road_rub)}</b>. НДС в источнике не указан.</p>
      <p>{estimate.trucks} маш. по {money(estimate.forecast_unit_rub)}. Расчёт по весу и объёму
        при равномерной раскладке, до {estimate.tonnes_per_truck} т и {estimate.assumed_m3_per_truck} м³ на машину.</p>
      <p className="table-note">
        База {estimate.base_year} года — {money(estimate.base_unit_rub)} за машину. Услуги сверх автоплеча
        требуют отдельной оценки. Перевозчик подтверждает раскладку, цену партии и наличие машин.
        {" "}<a href={estimate.tariff_source.url}>Прайс направления</a>
        {" · "}<a href={estimate.capacity_source.url}>Параметры фур</a>
      </p>
    </section>
  );
}

export function PublishedCoverage({ result, compact = false }: { result: Calculation; compact?: boolean }) {
  const coverage = result.published_coverage ?? result.published_reference?.coverage;
  if (!coverage) return null;
  const road = coverage.truck_path;
  const vat = result.request.cost_basis === "including_vat" ? "с НДС" : "без НДС";
  const candidates = result.published_reference?.candidates ?? result.candidates;
  const rows = compact ? candidates.slice(0, 3) : candidates;
  return (
    <section className="notice">
      <h2>Опубликованные данные</h2>
      {road.status === "found" && road.distance_km != null && road.navigation_hours != null ? (
        <p>Путь по грузовому графу · {num(road.distance_km, 1)} км · {hours(road.navigation_hours)} в движении.
          {" "}<a href="https://www.openstreetmap.org/copyright">© OpenStreetMap</a></p>
      ) : roadStatus[road.status] && <p>{roadStatus[road.status]}</p>}
      {!rows.length && <p>В наземных прайсах нет ставки для выбранных условий.</p>}
      {coverage.terminals?.map((terminal) => (
        <p key={terminal.id + terminal.operation}>
          {terminal.operation === "pickup" ? "Подвоз" : "Довоз"} · {terminal.name}, ст. {terminal.station}.{" "}
          {terminal.path.status === "found" && terminal.path.distance_km != null
            ? `${num(terminal.path.distance_km, 1)} км. Нужна котировка.`
            : roadStatus[terminal.path.status]}
        </p>
      ))}
      {rows.map((candidate) => {
        const rate = candidate.published_rate;
        if (!rate) return null;
        const tax = rate.vat === "included" ? "с НДС" : "НДС не указан";
        return (
          <div className="source-item" key={candidate.id}>
            <b>{candidate.route_hubs.map((hub) => hub.name).join(" → ")} · {candidate.modes.map((mode) => modeNames[mode]).join(" → ")}</b>
            <p>{rate.equipment} · {money(rate.published_amount_rub)} за единицу ({tax}).{" "}
              <a href={rate.source_url} target="_blank" rel="noreferrer">Источник · сверено {rate.observed_on}</a>
              {rate.valid_from ? ` · опубликовано начало действия ${rate.valid_from}` : " · период действия не указан"}</p>
            {rate.forecast_amount_rub != null && <p>Прогноз по опубликованной базе на {rate.forecast_year} · {money(rate.forecast_amount_rub)} за единицу ({tax})</p>}
            {rate.scope && railScopes[rate.scope] && <p>Состав услуги · {railScopes[rate.scope]}</p>}
            {(rate.origin_station || rate.destination_station) && <p>Станции · {rate.origin_station ?? "по согласованию"} → {rate.destination_station ?? "по согласованию"}</p>}
            {candidate.known_cost_subtotal_rub != null && <p>Частичная смета · {money(candidate.known_cost_subtotal_rub)} {vat}</p>}
            {rate.distance_km != null && <p>Расстояние из прайса · {num(rate.distance_km, 1)} км</p>}
            {rate.transit_days?.length ? <p>Ориентир из прайса · {rate.transit_days.join("–")} сут.</p> : null}
            {rate.security_rub != null && <p>Отдельно по прайсу · охрана {money(rate.security_rub)} за контейнер</p>}
            {!compact && rate.separate_services?.map((service) => (
              <p className="table-note" key={service.name}>
                {service.name} · от {money(service.from_rub)}{service.unit === "day" ? " в сутки" : ""}. Уточните применимость.
              </p>
            ))}
            {candidate.issues.map((issue) => <p className="table-note" key={issue.code}>{issue.message}</p>)}
          </div>
        );
      })}
      {!compact && coverage.other_tariffs.length > 0 && (
        <details>
          <summary>Справочные прайсы на этом направлении</summary>
          {coverage.other_tariffs.map((row, index) => (
            <p key={index}>{row.origin} → {row.destination}.{" "}
              {row.from_rub_per_kg != null && `От ${money(row.from_rub_per_kg)}/кг. `}
              {row.from_rub_per_m3 != null && `От ${money(row.from_rub_per_m3)}/м³. `}
              {row.amount_rub != null && `${row.equipment} · ${money(row.amount_rub)}. Состав плеч уточняется. `}
              <a href={row.source_url} target="_blank" rel="noreferrer">Условия перевозчика</a></p>
          ))}
        </details>
      )}
      <details>
        <summary>Фактические объёмы перевозок</summary>
        <p>В каталоге нет ряда объёмов по этому направлению. Ниже показана статистика Росстата по России.</p>
        {coverage.volume.observations.map((row) => (
          <p key={`${row.year}:${row.mode}:${row.indicator}`}>
            {row.year} · {row.geography} · {modeNames[row.mode]} · {num(row.value, 1)}
            {" "}{row.unit === "million_tonnes" ? "млн т" : "млрд т·км"}
          </p>
        ))}
        <a href="https://rosstat.gov.ru/storage/mediabank/Rossia_2025.pdf">Росстат, «Россия 2025», стр. 78</a>
      </details>
    </section>
  );
}
