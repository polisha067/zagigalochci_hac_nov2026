import os
import unittest
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.network.delivery_forecast import ForecastRequest, calculate_forecast
from backend.network.tlm_tariff import bundled_road_tariff
from backend.update_tlm_tariff import refresh
from tests.test_delivery_forecast import shipment


def price_page(year=2027, *, bryansk=80000):
    rows = "".join(
        f"<tr><td>{escape(city)}</td><td>{bryansk if city == 'Брянск' else rate:,} ₽</td></tr>".replace(",", " ")
        for city, rate in bundled_road_tariff()["rows"]
    )
    return f"""
        <ul><li>Транспорт: Тентованные полуприцепы (20 тонн / 82-92 м³).</li></ul>
        <h2>Тарифы на перевозку из Москвы ({year} год)</h2>
        <table><tr><th>Направление (Город назначения)</th><th>Ставка (с НДС 22%)</th></tr>{rows}</table>
        <h3>В стоимость включено:</h3>
        <ul><li>НДС 22% и топливный сбор.</li><li>Экспедирование груза водителем.</li>
        <li>Оформление сопроводительных документов.</li><li>Страхование груза (до 5 млн. руб.).</li>
        <li>Мониторинг движения ТС.</li></ul>
    """


class TlmRoadRefreshTests(unittest.TestCase):
    def test_forecast_uses_each_version_from_its_applicable_date(self):
        request = ForecastRequest.model_validate(shipment(
            destination="Брянск", allowed_modes=["road"], target_year=2030, max_legs=1))

        def movement():
            result = calculate_forecast(request)
            row = next(row for row in result.candidates if row["id"] == result.recommendation)
            return next(item for item in row["cost_items"] if item["kind"] == "transport")

        with TemporaryDirectory() as folder, patch.dict(os.environ, {"TLM_TARIFF_DIR": folder}), \
                patch("backend.network.russia.moscow_today", return_value=date(2026, 11, 12)) as today, \
                patch("backend.update_tlm_tariff.fetch_page") as fetch:
            directory = Path(folder)
            self.assertEqual(movement()["base_amount_rub"], 65000)
            fetch.return_value = price_page(2026, bryansk=72000)
            refresh(directory, road=True, now=datetime(2026, 11, 13, tzinfo=timezone.utc))
            self.assertEqual(movement()["base_amount_rub"], 65000)
            today.return_value = date(2026, 11, 13)
            self.assertEqual(movement()["base_amount_rub"], 72000)

            fetch.return_value = price_page()
            now = datetime(2026, 12, 29, tzinfo=timezone.utc)
            path = refresh(directory, road=True, now=now)
            self.assertIsNone(refresh(directory, road=True, now=now))
            self.assertEqual(movement()["base_amount_rub"], 72000)
            today.return_value = date(2027, 1, 1)
            item = movement()
            self.assertEqual(item["base_amount_rub"], 80000)
            self.assertEqual([factor["year"] for factor in item["index_factors"]], [2028, 2029, 2030])
            self.assertEqual(item["pricing"]["observed_on"], "2026-12-29")
            self.assertEqual(item["pricing"]["tariff_date_precision"], "year")
            self.assertIn(item["pricing"]["tariff_version"], path.name)

            for page in (
                price_page().replace("<td>Брянск</td>", "<td>Астрахань</td>"),
                price_page().replace("<tr><td>Брянск</td><td>80 000 ₽</td></tr>", ""),
                price_page().replace("НДС 22%", "НДС 20%"),
                price_page().replace("20 тонн", "10 тонн"),
                price_page(bryansk=0),
                price_page(2026),
            ):
                fetch.return_value = page
                with self.assertRaises(ValueError):
                    refresh(directory, road=True, now=now)
            fetch.side_effect = OSError("Источник недоступен")
            with self.assertRaises(OSError):
                refresh(directory, road=True, now=now)
            self.assertEqual(len(list(directory.glob("tlm-road-*.json"))), 2)
            self.assertEqual(movement(), item)
