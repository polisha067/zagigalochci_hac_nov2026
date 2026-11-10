import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { ApiError, errorText, post } from "./api";
import type {
  Calculation,
  Clarification,
  FrequencyUnit,
  Mode,
  Options,
} from "./networkApi";
import {
  buildRequest,
  buildSchedule,
  modeNames,
  newCargo,
  newStop,
  num,
  packagingNames,
  scheduleEvents,
  selectedForecastYear,
  unitNames,
  type Cargo,
  type Draft,
} from "./model";
import { SettlementPicker } from "./SettlementPicker";
export function ConditionsForm({
  initial,
  options,
  onDraft,
  onResult,
}: {
  initial: Draft;
  options: Options;
  onDraft: (d: Draft) => void;
  onResult: (r: Calculation, d: Draft) => void;
}) {
  const [draft, setDraft] = useState(() => structuredClone(initial));
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [questions, setQuestions] = useState<Clarification[]>([]);
  const [retryAt, setRetryAt] = useState(0);
  const [now, setNow] = useState(Date.now());
  const inFlight = useRef<AbortController | null>(null);
  const form = useRef<HTMLFormElement>(null);
  const cargoListId = useId(),
    profilesId = useId();
  useEffect(() => () => inFlight.current?.abort(), []);
  useEffect(() => {
    onDraft(draft);
    setMessage("");
  }, [draft, onDraft]);
  useEffect(() => {
    if (!retryAt) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [retryAt]);
  const wait = Math.max(0, Math.ceil((retryAt - now) / 1000));
  const update = (change: Partial<Draft>) =>
    setDraft((d) => ({ ...d, ...change }));
  const cargoUpdate = (id: string, change: Partial<Cargo>) =>
    setDraft((d) => ({
      ...d,
      cargo: d.cargo.map((c) => (c.id === id ? { ...c, ...change } : c)),
    }));
  const points = [
    { id: "first", city: draft.origin },
    ...draft.stops,
    { id: "last", city: draft.destination },
  ];
  const forecastOptions = options.delivery_forecast;
  const forecastYear = selectedForecastYear(draft, forecastOptions);
  const preview = useMemo(() => {
    if (forecastYear && draft.scheduleMode === "once" && !draft.start)
      return { count: 1, error: "" };
    try {
      return { count: scheduleEvents(buildSchedule(draft)).length, error: "" };
    } catch (e) {
      return { count: 0, error: errorText(e) };
    }
  }, [
    draft.scheduleMode,
    draft.start,
    draft.count,
    draft.every,
    draft.unit,
    draft.period,
    draft.periodUnit,
    draft.dates,
    forecastYear,
  ]);
  const weight = draft.cargo.reduce((sum, c) => sum + Number(c.weight), 0);
  const volumeReady =
    preview.count > 0 && draft.cargo.every((c) => Number(c.weight) > 0);
  const complete =
    points.every((p) => p.city) &&
    draft.cargo.every((c) => c.name.trim() && Number(c.weight) > 0) &&
    (Number(draft.days) > 0 || Boolean(forecastYear && !draft.days));
  function removeStop(id: string) {
    setDraft((d) => ({
      ...d,
      stops: d.stops.filter((s) => s.id !== id),
      cargo: d.cargo.map((c) => ({
        ...c,
        from: c.from === id ? "first" : c.from,
        to: c.to === id ? "last" : c.to,
      })),
    }));
  }
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy || wait > 0) return;
    setMessage("");
    setBusy(true);
    const controller = new AbortController();
    inFlight.current = controller;
    try {
      const payload = buildRequest(draft, forecastOptions);
      const result = await post<Calculation>(
        payload.target_year ? "/network/russia/forecasts" : "/network/russia/calculations",
        payload,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      if (result.input_status === "needs_clarification") {
        setQuestions(result.clarifications);
        requestAnimationFrame(() =>
          form.current
            ?.querySelector<HTMLElement>(".cargo-questions, .form-questions")
            ?.scrollIntoView({ block: "center", behavior: "smooth" }),
        );
      } else {
        setQuestions([]);
        onResult(result, draft);
      }
    } catch (error) {
      if (controller.signal.aborted) return;
      setMessage(errorText(error));
      if (error instanceof ApiError && error.retryAfter) {
        setRetryAt(Date.now() + error.retryAfter * 1000);
        setNow(Date.now());
      }
    } finally {
      setBusy(false);
    }
  }
  function numberField(
    c: Cargo,
    key: keyof Cargo,
    label: string,
    min = "0.000001",
  ) {
    return (
      <label>
        {label}
        <input
          type="number"
          min={min}
          max="1000000"
          step="any"
          value={c[key]}
          onChange={(e) => cargoUpdate(c.id, { [key]: e.target.value })}
        />
      </label>
    );
  }
  return (
    <form
      ref={form}
      className="conditions-form network-form"
      noValidate
      onSubmit={submit}
    >
      <fieldset className="ticket" disabled={busy}>
        <section className="paper">
          <h1>Условия перевозки</h1>
          <div className="route">
            <SettlementPicker
              label="Откуда"
              value={draft.origin}
              initial={options.settlements}
              onChange={(origin) => update({ origin })}
            />
            <button
              className="addStop"
              type="button"
              disabled={draft.stops.length >= 8}
              onClick={() => update({ stops: [...draft.stops, newStop()] })}
            >
              + Остановка
            </button>
            <SettlementPicker
              label="Куда"
              value={draft.destination}
              initial={options.settlements}
              onChange={(destination) => update({ destination })}
            />
          </div>
          {draft.stops.map((stop, i) => (
            <div className="network-stop" key={stop.id}>
              <SettlementPicker
                label={`Остановка ${i + 1}`}
                value={stop.city}
                initial={options.settlements}
                onChange={(city) =>
                  update({
                    stops: draft.stops.map((s) =>
                      s.id === stop.id ? { ...s, city } : s,
                    ),
                  })
                }
              />
              <label>
                Доп. пребывание, ч
                <input
                  type="number"
                  min="0"
                  max="87600"
                  step="any"
                  placeholder="0"
                  value={stop.dwell}
                  onChange={(e) =>
                    update({
                      stops: draft.stops.map((s) =>
                        s.id === stop.id ? { ...s, dwell: e.target.value } : s,
                      ),
                    })
                  }
                />
              </label>
              <button
                type="button"
                className="text-button"
                aria-label={`Удалить остановку ${i + 1}`}
                onClick={() => removeStop(stop.id)}
              >
                ×
              </button>
              <small>
                Погрузка и выгрузка определяются пунктами каждой позиции ниже;
                без них — обязательное посещение.
              </small>
            </div>
          ))}
          <div className="columns">
            <section>
              <h2>Состав партии</h2>
              <datalist id={cargoListId}>
                {options.cargo_types.map((c) => (
                  <option key={c.id} value={c.name} />
                ))}
              </datalist>
              <datalist id={profilesId}>
                {options.cargo_input.handling_profiles.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </datalist>
              {draft.cargo.map((c, i) => {
                const qs = questions.filter((q) => q.cargo_id === c.id);
                const propsRequired = qs.some((q) =>
                  q.fields?.some((f) => f.startsWith("properties")),
                );
                return (
                  <div className="cargo network-cargo" key={c.id}>
                    <div className="position">
                      Позиция {i + 1}
                      {draft.cargo.length > 1 && (
                        <button
                          type="button"
                          aria-label={`Удалить позицию ${i + 1}`}
                          onClick={() =>
                            update({
                              cargo: draft.cargo.filter(
                                (item) => item.id !== c.id,
                              ),
                            })
                          }
                        >
                          ×
                        </button>
                      )}
                    </div>
                    <label>
                      Что перевозим?
                      <input
                        aria-label={`Название груза ${i + 1}`}
                        list={cargoListId}
                        maxLength={100}
                        placeholder="Введите название груза"
                        value={c.name}
                        onChange={(e) =>
                          cargoUpdate(c.id, { name: e.target.value })
                        }
                      />
                    </label>
                    {qs.length > 0 && (
                      <div className="cargo-questions" role="status">
                        <b>Уточните для этого груза</b>
                        <ul>
                          {qs.map((q, k) => (
                            <li key={k}>{q.message}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {numberField(c, "weight", "Вес за отправку, т")}
                    {numberField(c, "volume", "Объём за отправку, м³")}
                    <label>
                      Упаковка
                      <select
                        aria-label="Упаковка"
                        value={c.packaging}
                        onChange={(e) =>
                          cargoUpdate(c.id, { packaging: e.target.value })
                        }
                      >
                        <option value="">Не указана</option>
                        {[
                          ...new Set([
                            ...options.cargo_input.packagings,
                            "boxes",
                            "container20",
                            "container40",
                            "bulk",
                          ]),
                        ].map((p) => (
                          <option key={p} value={p}>
                            {packagingNames[p] || p}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Можно разделить позицию?
                      <select
                        aria-label="Можно разделить позицию?"
                        value={c.splittable}
                        onChange={(e) =>
                          cargoUpdate(c.id, {
                            splittable: e.target.value as Cargo["splittable"],
                          })
                        }
                      >
                        <option value="">Не указано</option>
                        <option value="yes">
                          Да, между машинами / единицами
                        </option>
                        <option value="no">Нет, позиция неделимая</option>
                      </select>
                    </label>
                    {draft.stops.length > 0 &&
                      (["from", "to"] as const).map((key) => (
                        <label key={key}>
                          {key === "from" ? "Погрузка" : "Выгрузка"}
                          <select
                            aria-label={`${key === "from" ? "Погрузка" : "Выгрузка"} груза ${i + 1}`}
                            value={c[key]}
                            onChange={(e) =>
                              cargoUpdate(c.id, { [key]: e.target.value })
                            }
                          >
                            {points.map((p, k) => (
                              <option key={p.id} value={p.id}>
                                {k + 1}. {p.city?.name || "Выберите пункт"}
                              </option>
                            ))}
                          </select>
                        </label>
                      ))}
                    <details
                      className="cargo-properties"
                      open={propsRequired || undefined}
                    >
                      <summary>Свойства и требования к грузу</summary>
                      <p className="table-note">
                        Для груза вне справочника подтвердите типовые условия
                        или укажите свойства вручную.
                      </p>
                      {propsRequired && ![c.phase, c.hazardous, c.profile, c.temperatureRequired].some(Boolean) && (
                        <button
                          type="button"
                          className="text-button"
                          onClick={() =>
                            cargoUpdate(c.id, {
                              phase: "solid",
                              hazardous: "no",
                              profile: "dry_general",
                              temperatureRequired: "no",
                            })
                          }
                        >
                          Подтвердить сухой неопасный груз без терморежима
                        </button>
                      )}
                      <label>
                        Состояние
                        <select
                          aria-label="Состояние"
                          value={c.phase}
                          onChange={(e) =>
                            cargoUpdate(c.id, {
                              phase: e.target.value as Cargo["phase"],
                            })
                          }
                        >
                          <option value="">Не указано</option>
                          <option value="solid">Твёрдый груз</option>
                          <option value="liquid">Жидкость</option>
                          <option value="gas">Газ</option>
                        </select>
                      </label>
                      <label>
                        Опасный груз?
                        <select
                          aria-label="Опасный груз?"
                          value={c.hazardous}
                          onChange={(e) =>
                            cargoUpdate(c.id, {
                              hazardous: e.target.value as Cargo["hazardous"],
                            })
                          }
                        >
                          <option value="">Не указано</option>
                          <option value="yes">Да</option>
                          <option value="no">Нет</option>
                        </select>
                      </label>
                      <label>
                        Условия обращения
                        <input
                          list={profilesId}
                          maxLength={100}
                          placeholder="Выберите профиль или введите свой"
                          value={c.profile}
                          onChange={(e) =>
                            cargoUpdate(c.id, { profile: e.target.value })
                          }
                        />
                      </label>
                      <p className="table-note">
                        {options.cargo_input.handling_profiles
                          .map((p) => `${p.id} — ${p.name}`)
                          .join("; ")}
                        .
                      </p>
                      <label>
                        Нужен температурный режим?
                        <select
                          aria-label="Нужен температурный режим?"
                          value={c.temperatureRequired}
                          onChange={(e) =>
                            cargoUpdate(c.id, {
                              temperatureRequired: e.target
                                .value as Cargo["temperatureRequired"],
                            })
                          }
                        >
                          <option value="">Не указано</option>
                          <option value="yes">Да</option>
                          <option value="no">Нет</option>
                        </select>
                      </label>
                      {c.temperatureRequired === "yes" && (
                        <div className="paired-fields">
                          {numberField(
                            c,
                            "minTemp",
                            "Температура от, °C",
                            "-1000",
                          )}
                          {numberField(
                            c,
                            "maxTemp",
                            "Температура до, °C",
                            "-1000",
                          )}
                        </div>
                      )}
                      {numberField(c, "pieceWeight", "Масса одного места, т")}
                      <div className="paired-fields">
                        {numberField(c, "length", "Длина места, м")}
                        {numberField(c, "width", "Ширина места, м")}
                        {numberField(c, "height", "Высота места, м")}
                      </div>
                      {numberField(
                        c,
                        "readyOffset",
                        "Готовность позже отправки, ч",
                        "0",
                      )}
                      {numberField(
                        c,
                        "cargoHours",
                        "Макс. срок этой позиции, ч",
                      )}
                      {numberField(c, "shelfHours", "Срок годности, ч")}
                    </details>
                  </div>
                );
              })}
              <button
                className="link"
                type="button"
                disabled={draft.cargo.length >= 20}
                onClick={() => update({ cargo: [...draft.cargo, newCargo()] })}
              >
                + Добавить груз
              </button>
            </section>
            <section>
              <h2>График отправок</h2>
              {forecastOptions && (
                <label className="schedule-field">
                  Год прогноза
                  <select
                    value={draft.forecastYear ?? ""}
                    onChange={(e) => {
                      const year = e.target.value;
                      update(year
                        ? { forecastYear: year, scheduleMode: "once", start: "", capacityBasis: "mass_and_volume" }
                        : { forecastYear: "", start: draft.start || initial.start });
                    }}
                  >
                    <option value="">По дате отправки</option>
                    {Array.from({ length: forecastOptions.max_year - forecastOptions.min_year + 1 }, (_, index) => {
                      const year = forecastOptions.min_year + index;
                      return <option key={year} value={year}>{year}</option>;
                    })}
                  </select>
                </label>
              )}
              <div className="toggle schedule-toggle">
                {(["once", "regular", "explicit"] as const).map((mode, i) => (
                  <button
                    type="button"
                    key={mode}
                    aria-pressed={draft.scheduleMode === mode}
                    onClick={() => update({ scheduleMode: mode })}
                  >
                    {["Разовая", "Регулярная", "По датам"][i]}
                  </button>
                ))}
              </div>
              {draft.scheduleMode !== "explicit" && (
                <label className="schedule-field">
                  {forecastYear && draft.scheduleMode === "once" ? "Дата, МСК (необязательно)" : "Начало, МСК"}
                  <input
                    type="datetime-local"
                    min="2000-01-01T00:00"
                    max="2100-12-31T23:59"
                    value={draft.start}
                    onChange={(e) => update({ start: e.target.value })}
                  />
                </label>
              )}
              {draft.scheduleMode === "regular" && (
                <>
                  <label className="schedule-field">
                    Отправок за интервал
                    <input
                      type="number"
                      min="1"
                      max="168"
                      placeholder="Например, 2"
                      value={draft.count}
                      onChange={(e) => update({ count: e.target.value })}
                    />
                  </label>
                  <div className="paired-fields">
                    <label>
                      Каждые
                      <input
                        type="number"
                        min="1"
                        max="3650"
                        value={draft.every}
                        onChange={(e) => update({ every: e.target.value })}
                      />
                    </label>
                    <label>
                      Единица интервала
                      <select
                        aria-label="Единица интервала"
                        value={draft.unit}
                        onChange={(e) =>
                          update({ unit: e.target.value as FrequencyUnit })
                        }
                      >
                        {options.schedule_options.units.map((u) => (
                          <option key={u} value={u}>
                            {unitNames[u]}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                  <div className="paired-fields">
                    <label>
                      Период
                      <input
                        type="number"
                        min="1"
                        placeholder="Например, 4"
                        value={draft.period}
                        onChange={(e) => update({ period: e.target.value })}
                      />
                    </label>
                    <label>
                      Единица периода
                      <select
                        aria-label="Единица периода"
                        value={draft.periodUnit}
                        onChange={(e) =>
                          update({
                            periodUnit: e.target.value as Draft["periodUnit"],
                          })
                        }
                      >
                        {(["hour", "day", "week", "month"] as const).map(
                          (u) => (
                            <option key={u} value={u}>
                              {unitNames[u]}
                            </option>
                          ),
                        )}
                      </select>
                    </label>
                  </div>
                  <p className="table-note">
                    Отправки равномерно распределяются внутри интервала. Конец
                    периода не включается.
                  </p>
                </>
              )}
              {draft.scheduleMode === "explicit" && (
                <div className="explicit-dates">
                  {draft.dates.map((value, i) => (
                    <div className="explicit-date" key={i}>
                      <label>
                        Отправка {i + 1}, МСК
                        <input
                          type="datetime-local"
                          value={value}
                          min="2000-01-01T00:00"
                          max="2100-12-31T23:59"
                          onChange={(e) =>
                            update({
                              dates: draft.dates.map((v, k) =>
                                k === i ? e.target.value : v,
                              ),
                            })
                          }
                        />
                      </label>
                      {draft.dates.length > 1 && (
                        <button
                          type="button"
                          className="text-button"
                          aria-label={`Удалить дату ${i + 1}`}
                          onClick={() =>
                            update({
                              dates: draft.dates.filter((_, k) => k !== i),
                            })
                          }
                        >
                          ×
                        </button>
                      )}
                    </div>
                  ))}
                  <button
                    className="link"
                    type="button"
                    disabled={draft.dates.length >= 64}
                    onClick={() => update({ dates: [...draft.dates, ""] })}
                  >
                    + Добавить дату
                  </button>
                </div>
              )}
              <label className="schedule-field">
                Макс. срок, дней
                <input
                  type="number"
                  min="0.000001"
                  max="3650"
                  step="any"
                  placeholder="Например, 2,5"
                  value={draft.days}
                  onChange={(e) => update({ days: e.target.value })}
                />
              </label>
              <fieldset className="mode-choice">
                <legend>Виды транспорта</legend>
                {(Object.keys(modeNames) as Mode[]).map((mode) => (
                  <label key={mode}>
                    <input
                      type="checkbox"
                      checked={draft.modes.includes(mode)}
                      onChange={(e) =>
                        update({
                          modes: e.target.checked
                            ? [...draft.modes, mode]
                            : draft.modes.filter((m) => m !== mode),
                        })
                      }
                    />
                    {modeNames[mode]}
                  </label>
                ))}
              </fieldset>
              <details
                className="calculation-settings"
                open={
                  questions.some((q) => q.field === "mass_basis") || undefined
                }
              >
                <summary>Условия расчёта</summary>
                <label>
                  Проверка вместимости
                  <select
                    aria-label="Проверка вместимости"
                    value={draft.capacityBasis}
                    onChange={(e) =>
                      update({
                        capacityBasis: e.target.value as Draft["capacityBasis"],
                      })
                    }
                  >
                    <option value="mass_only">По массе</option>
                    <option value="mass_and_volume">По массе и объёму</option>
                  </select>
                </label>
                <p className="table-note">
                  При проверке только по массе известный объём всё равно
                  учитывается. Для авиа объём обязателен.
                </p>
                <label>
                  Стоимость
                  <select
                    aria-label="Стоимость"
                    value={draft.costBasis}
                    onChange={(e) =>
                      update({
                        costBasis: e.target.value as Draft["costBasis"],
                      })
                    }
                  >
                    <option value="including_vat">С НДС</option>
                    <option value="excluding_vat">Без НДС</option>
                  </select>
                </label>
                <label>
                  Допустимые данные
                  <select
                    aria-label="Допустимые данные"
                    value={draft.dataPolicy}
                    onChange={(e) =>
                      update({
                        dataPolicy: e.target.value as Draft["dataPolicy"],
                      })
                    }
                  >
                    <option value="allow_assumptions">
                      Разрешить явные допущения
                    </option>
                    <option value="verified_only">
                      Только подтверждённые данные
                    </option>
                  </select>
                </label>
                <label>
                  Основа массы
                  <select
                    aria-label="Основа массы"
                    value={draft.massBasis}
                    onChange={(e) =>
                      update({
                        massBasis: e.target.value as Draft["massBasis"],
                      })
                    }
                  >
                    <option value="">Не указана</option>
                    <option value="gross">Брутто</option>
                    <option value="net">Нетто, без балласта</option>
                  </select>
                </label>
              </details>
            </section>
          </div>
        </section>
        <aside>
          <div className="network-coupon">
            <div className="stub-title">
              <span className="target" aria-hidden="true" />
              Плановый объём
            </div>
            <div
              className={`volume${volumeReady ? "" : " volume-empty"}`}
              aria-live="polite"
            >
              {volumeReady ? num(weight * preview.count, 6) : "—"}{" "}
              <small>т</small>
            </div>
            <p className="period">
              {draft.scheduleMode === "once"
                ? "За одну отправку"
                : "За выбранный период"}
            </p>
            <div className="summary">
              <span>Отправок</span>
              <b>{preview.count || "—"}</b>
              <span>Партия</span>
              <b>{weight > 0 ? num(weight, 6) + " т" : "—"}</b>
            </div>
            {preview.error &&
              draft.start &&
              (draft.count || draft.scheduleMode !== "regular") && (
                <p className="plan-hint">{preview.error}</p>
              )}
            <div className="action">
              <p>Расчёт по транспортной сети · до 64 отправок</p>
              <p>
                {draft.dataPolicy === "allow_assumptions"
                  ? "Сценарные тарифы и ресурсы будут отмечены в отчёте."
                  : "Неподтверждённые условия не допускаются."}
              </p>
              <button
                className="primary"
                type="submit"
                disabled={!complete || busy || wait > 0}
              >
                {busy
                  ? "Ищем варианты…"
                  : wait > 0
                    ? `Повторить через ${wait} с`
                    : "Рассчитать"}{" "}
                <span aria-hidden="true">→</span>
              </button>
            </div>
          </div>
        </aside>
      </fieldset>
      {questions.length > 0 && (
        <div className="feedback form-questions" role="status">
          <b>Для расчёта нужны уточнения</b>
          <p>
            Заполненные условия сохранены. Ответьте на вопросы и нажмите
            «Рассчитать» ещё раз.
          </p>
          {questions
            .filter((q) => !q.cargo_id)
            .map((q, i) => (
              <p key={i}>{q.message}</p>
            ))}
        </div>
      )}
      {message && (
        <p role="alert" className="feedback error">
          {message}
        </p>
      )}
    </form>
  );
}
