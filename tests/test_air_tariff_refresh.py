import os
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.network.air_tariff import PAGE_URL, bundled_air_tariff
from backend.network.delivery_forecast import ForecastRequest, calculate_forecast
from backend.update_air_tariff import refresh_air
from backend.update_tlm_tariff import publish_snapshot
from tests.test_delivery_forecast import shipment


class AirTariffRefreshTests(unittest.TestCase):
    def test_new_release_preserves_period_weight_break_and_last_good_version(self):
        pdf_url = PAGE_URL + "test-release.pdf"
        html = (f'<a href="{pdf_url}">Грузовые тарифы на внутренние воздушные линии '
                'февраль–апрель 2027</a>').encode()
        header = ["Аэропорт отправления", "Аэропорт назначения", "-10 кг. руб./кг.",
                  "+10 кг. руб./кг.", "+100 кг. руб./кг.", "+300 кг. руб./кг."]
        terms = (
            "Грузовые тарифы-НЕТТО на внутренние воздушные линии, в т.ч. НДС БЕЗ УЧЕТА УСЛУГ АГЕНТА "
            "Минимально оплачиваемый вес (M) составляет – 10 (десять) кг. "
            "Объемный вес=дл(см)*ш(см)*в(см)/6000 куб.см/кг. "
            "Значение реального веса округляется до ближайших больших 0,5 кг. "
            "Дополнительно взимается сбор за оформленную грузовую накладную (AWB) – 600 руб."
        )
        row = ["Москва (МОВ)", "Якутск (ЯКТ)", "310,00", "305,00", "300,00", "290,00"]
        old = bundled_air_tariff()

        def forecast(day, weight=.1, destination="Якутск"):
            payload = shipment(destination=destination, allowed_modes=["air"], max_legs=1, target_year=2027,
                               schedule={"mode": "once", "ready_at": f"2027-{day}T12:00:00+09:00"})
            payload["cargoes"][0].update(weight_tonnes=weight, volume_m3=.2)
            return calculate_forecast(ForecastRequest.model_validate(payload))

        with TemporaryDirectory() as folder, patch.dict(os.environ, {"TLM_TARIFF_DIR": folder}), \
                patch("backend.network.russia.yakutsk_today", return_value=date(2027, 1, 31)) as today, \
                patch("backend.update_air_tariff.download", side_effect=lambda url, _limit:
                      (html, "utf-8") if url == PAGE_URL else (b"%PDF-test", None)), \
                patch("backend.update_air_tariff.read_pdf", return_value=([terms], [[header, row]])) as pdf:
            directory = Path(folder)
            checked = datetime(2027, 1, 30, tzinfo=timezone.utc)
            path = refresh_air(directory, publish_snapshot, now=checked)
            self.assertIsNone(refresh_air(directory, publish_snapshot, now=checked))
            self.assertEqual(forecast("02-01").candidates[0]["forecast_price"]["air_base_rub"], 27500)
            today.return_value = date(2027, 2, 1)
            self.assertEqual(forecast("01-31").candidates[0]["cost_items"][0]["pricing"]["tariff_version"], old["version"])
            active = forecast("02-01")
            item, = active.candidates[0]["cost_items"]
            self.assertEqual(item["base_amount_rub"], 30600)
            self.assertEqual(item["amount_rub"], 30600)
            self.assertEqual(item["index_factors"], [])
            self.assertEqual(item["pricing"]["valid_until"], "2027-05-01T00:00:00+09:00")
            self.assertIn(item["pricing"]["tariff_version"], path.name)
            self.assertEqual(forecast("02-01", .099).candidates[0]["forecast_price"]["air_base_rub"], 30795)
            self.assertGreater(forecast("05-01").candidates[0]["cost_items"][0]["amount_rub"], 30600)
            self.assertEqual(forecast("02-01", destination="Новосибирск").candidates, [])

            pdf.return_value = ([terms], [[header, row[:-1]]])
            with self.assertRaisesRegex(ValueError, "Неполная строка"):
                refresh_air(directory, publish_snapshot, now=checked)
            self.assertEqual(list(directory.glob("yakutia-*.json")), [path])
            self.assertEqual(forecast("02-01").candidates, active.candidates)
