import { useId, useState } from "react";
import { num } from "./model";

export interface ChartPoint {
  label: string;
  value: number;
  lower?: number | null;
  upper?: number | null;
}
const compact = (value: number) =>
  new Intl.NumberFormat("ru-RU", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
function niceMaximum(value: number) {
  if (value <= 0) return 1;
  const step = 10 ** Math.floor(Math.log10(value));
  return Math.ceil((value * 1.12) / step / 0.25) * step * 0.25;
}

/** Шкалы начинаются с нуля. Точки и интервалы берутся из API, сглаживание не добавляет значений. */
export function ForecastChart({
  points,
  unit,
  label,
  variant = "line",
}: {
  points: ChartPoint[];
  unit: string;
  label: string;
  variant?: "line" | "bar";
}) {
  const id = useId().replaceAll(":", "");
  const [hovered, setHovered] = useState<number | null>(null);
  const width = 720,
    height = 330,
    left = 76,
    right = 24,
    top = 42,
    bottom = 52;
  const plotWidth = width - left - right,
    floor = height - bottom;
  const maximum = niceMaximum(
    Math.max(0, ...points.map((p) => Math.max(p.value, p.upper ?? 0))),
  );
  const y = (value: number) => floor - (value / maximum) * (floor - top);
  const x = (index: number) =>
    points.length === 1
      ? left + plotWidth / 2
      : variant === "bar"
        ? left + (plotWidth * (index + 0.5)) / points.length
        : left + (plotWidth * index) / (points.length - 1);
  const path = points
    .map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.value)}`)
    .join(" ");
  const active = hovered == null ? null : points[hovered];
  return (
    <div className="data-chart" onMouseLeave={() => setHovered(null)}>
      <svg
        className="forecast-chart"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={label}
      >
        <defs>
          <linearGradient id={`area-${id}`} x1="0" y1="0" x2="0" y2="1">
            <stop stopColor="#0271ff" stopOpacity=".2" />
            <stop offset="1" stopColor="#0271ff" stopOpacity=".02" />
          </linearGradient>
        </defs>
        <text x={left} y="20" className="chart-unit">
          {unit}
        </text>
        {Array.from({ length: 5 }, (_, i) => {
          const value = (maximum * i) / 4;
          return (
            <g key={i}>
              <line
                x1={left}
                x2={width - right}
                y1={y(value)}
                y2={y(value)}
                className="chart-grid"
              />
              <text
                x={left - 12}
                y={y(value) + 4}
                textAnchor="end"
                className="chart-tick"
              >
                {compact(value)}
              </text>
            </g>
          );
        })}
        {variant === "line" && points.length > 1 && (
          <>
            <path
              d={`${path} L${x(points.length - 1)},${floor} L${x(0)},${floor} Z`}
              fill={`url(#area-${id})`}
            />
            <path
              d={path}
              stroke="#0271ff"
              strokeWidth="3"
              fill="none"
              strokeLinejoin="round"
            />
          </>
        )}
        {points.map((p, i) => {
          const selected = i === hovered;
          return (
            <g key={p.label}>
              {variant === "bar" ? (
                <rect
                  x={x(i) - 48}
                  y={y(p.value)}
                  width="96"
                  height={Math.max(0, floor - y(p.value))}
                  rx="7"
                  fill={i === 0 ? "#b6d0f2" : "#0271ff"}
                />
              ) : (
                <circle
                  cx={x(i)}
                  cy={y(p.value)}
                  r={selected ? 6 : 4}
                  fill="white"
                  stroke="#0271ff"
                  strokeWidth="2"
                />
              )}
              {p.lower != null && p.upper != null && (
                <g className="chart-interval">
                  <line x1={x(i)} x2={x(i)} y1={y(p.lower)} y2={y(p.upper)} />
                  <path
                    d={`M${x(i) - 9},${y(p.lower)}h18 M${x(i) - 9},${y(p.upper)}h18`}
                  />
                </g>
              )}
              {(variant === "bar" ||
                points.length <= 12 ||
                i === 0 ||
                i === points.length - 1 ||
                i % Math.ceil(points.length / 8) === 0) && (
                <text
                  x={x(i)}
                  y={floor + 26}
                  textAnchor="middle"
                  className="chart-tick"
                >
                  {p.label}
                </text>
              )}
              {variant === "bar" && (
                <text
                  x={x(i)}
                  y={Math.max(28, y(p.upper ?? p.value) - 12)}
                  textAnchor="middle"
                  className="chart-value"
                >
                  {num(p.value, 2)} {unit}
                </text>
              )}
              <rect
                x={
                  variant === "bar"
                    ? x(i) - 60
                    : x(i) - Math.max(12, plotWidth / points.length / 2)
                }
                y={top}
                width={
                  variant === "bar"
                    ? 120
                    : Math.max(24, plotWidth / points.length)
                }
                height={floor - top}
                fill="transparent"
                tabIndex={0}
                role="button"
                className="chart-hit"
                aria-label={`${p.label}: ${num(p.value, 2)} ${unit}${p.lower != null && p.upper != null ? `, диапазон ${num(p.lower, 2)} – ${num(p.upper, 2)}` : ""}`}
                onFocus={() => setHovered(i)}
                onBlur={() => setHovered(null)}
                onMouseEnter={() => setHovered(i)}
                onClick={() => setHovered(selected ? null : i)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") setHovered(null);
                }}
              />
            </g>
          );
        })}
        {hovered != null && variant === "line" && (
          <line
            x1={x(hovered)}
            x2={x(hovered)}
            y1={top}
            y2={floor}
            className="chart-guide"
          />
        )}
      </svg>
      {active && (
        <div
          className="chart-tooltip"
          role="status"
          style={{
            left: `${Math.min(76, Math.max(12, (x(hovered!) / width) * 100))}%`,
          }}
        >
          <span>{active.label}</span>
          <b>
            {num(active.value, 2)} {unit}
          </b>
          {active.lower != null && active.upper != null && (
            <small>
              Диапазон: {num(active.lower, 0)} – {num(active.upper, 0)} {unit}
            </small>
          )}
        </div>
      )}
    </div>
  );
}
