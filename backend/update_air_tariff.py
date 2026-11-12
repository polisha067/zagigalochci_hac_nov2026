"""Read and validate published Yakutia domestic freight tariff releases."""

import re
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from io import BytesIO
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import pdfplumber
from pdfminer.pdfexceptions import PDFException

from backend.network.air_tariff import (
    PAGE_URL, YAKUTSK, bundled_air_tariff, is_publisher_url, latest_air_snapshot, read_air_snapshot, version_of,
)

MONTHS = {name: index for index, name in enumerate(
    "январь февраль март апрель май июнь июль август сентябрь октябрь ноябрь декабрь".split(), 1)}
MONTH = "|".join(MONTHS)
RELEASE = re.compile(
    rf"грузовые тарифы на внутренние воздушные линии\s+({MONTH})"
    rf"(?:\s*[-–—]\s*({MONTH}))?\s+(20\d{{2}})(?:\s*г(?:од[а]?)?\.?)?", re.I)
AIRPORT = re.compile(r".+ \(([А-ЯЁA-Z]{3})\)")


class TariffLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.current = None
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.current = (dict(attrs).get("href"), [])

    def handle_data(self, data):
        if self.current:
            self.current[1].append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.current:
            href, label = self.current
            self.links.append((href, " ".join("".join(label).split())))
            self.current = None


def releases(html):
    parser = TariffLinks()
    parser.feed(html)
    parser.close()
    result = {}
    for href, label in parser.links:
        match = RELEASE.fullmatch(label)
        if not match or not href:
            continue
        url = urljoin(PAGE_URL, href)
        if not is_publisher_url(url):
            raise ValueError("Ссылка на авиатариф ведёт за пределы сайта Якутии")
        first, last, year = match.groups()
        year = int(year)
        first_month, last_month = MONTHS[first.lower()], MONTHS[(last or first).lower()]
        if last_month < first_month:
            raise ValueError("Неоднозначный год окончания авиатарифа")
        start = date(year, first_month, 1)
        end = date(year + int(last_month == 12), last_month % 12 + 1, 1)
        if (start, end) in result and result[start, end] != url:
            raise ValueError("Несколько авиатарифов с одним периодом действия")
        result[start, end] = url
    if not result:
        raise ValueError("На странице Якутии не найден датированный внутренний грузовой прайс")
    return result


def download(url, limit):
    if not is_publisher_url(url):
        raise ValueError("Неизвестный источник авиатарифа")
    request = Request(url, headers={"User-Agent": "Poputno tariff updater/1.0"})
    with urlopen(request, timeout=20) as response:
        if not is_publisher_url(response.geturl()):
            raise ValueError("Прайс Якутии перенаправлен на другой домен")
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Превышен допустимый размер ответа Якутии")
        return data, response.headers.get_content_charset()


def read_pdf(data):
    if not data.startswith(b"%PDF-"):
        raise ValueError("Якутия вернула файл в формате, отличном от PDF")
    try:
        with pdfplumber.open(BytesIO(data)) as pdf:
            if not 2 <= len(pdf.pages) <= 30:
                raise ValueError("Неожиданное число страниц авиатарифа")
            texts = [page.extract_text() or "" for page in pdf.pages]
            if not all(texts):
                raise ValueError("Часть страниц авиатарифа не читается")
            return texts, [table for page in pdf.pages for table in page.extract_tables()]
    except (PDFException, EOFError, TypeError) as exc:
        raise ValueError("PDF авиатарифа повреждён") from exc


def term(text, pattern, label):
    matches = re.findall(pattern, text, re.I)
    if len(matches) != 1:
        raise ValueError(f"Не удалось однозначно извлечь {label}")
    return matches[0]


def parse_pdf(data):
    texts, tables = read_pdf(data)
    text = " ".join(" ".join(texts).split())
    if not re.search(r"Грузовые тарифы\s*[-–—]\s*НЕТТО на внутренние воздушные линии,?\s+в т\.ч\. НДС\s+БЕЗ УЧЕТА УСЛУГ АГЕНТА", text, re.I):
        raise ValueError("Изменились налоговые условия или состав тарифа Якутии")
    minimum = int(term(text, r"Минимально оплачиваемый вес \(M\) составляет\s*[-–—]\s*(\d+)", "минимальный вес"))
    divisor = int(term(text, r"/\s*(\d+)\s+куб\.см/кг", "делитель объёмного веса"))
    step = float(term(text, r"реального веса округляется до ближайших больших\s+(\d+(?:[,.]\d+)?)\s*кг", "шаг округления").replace(",", "."))
    awb = int(term(text, r"сбор за оформленную грузовую накладную \(AWB\)\s*[-–—]\s*(\d+)\s*руб", "сбор за накладную"))
    if not all((minimum, divisor, step, awb)):
        raise ValueError("Нулевой параметр авиатарифа")

    rows = []
    seen = set()
    breakpoints = None
    for table in tables:
        header = [" ".join((cell or "").split()) for cell in table[0]]
        if header[:2] != ["Аэропорт отправления", "Аэропорт назначения"] or (
                len(header) > 2 and header[2] == "Характер груза"):
            continue
        if len(header) < 4:
            raise ValueError("Изменился формат таблицы весовых ступеней")
        bounds = [re.fullmatch(r"([+−–-])\s*(\d+)\s*кг\.?\s*руб\./кг\.?", value, re.I) for value in header[2:]]
        if (not all(bounds) or bounds[0][1] == "+" or int(bounds[0][2]) != minimum
                or bounds[1][1] != "+" or int(bounds[1][2]) != minimum or any(b[1] != "+" for b in bounds[1:])):
            raise ValueError("Изменились единицы или границы весовых ступеней")
        steps = [0, *(int(bound[2]) for bound in bounds[1:])]
        if steps != sorted(set(steps)) or (breakpoints is not None and breakpoints != steps):
            raise ValueError("Несогласованные весовые ступени в прайсе")
        breakpoints = steps
        for row in table[1:]:
            if len(row) != len(header) or any(cell is None for cell in row):
                raise ValueError("Неполная строка федерального авиатарифа")
            origin, destination, *values = (" ".join(cell.split()) for cell in row)
            airports = [AIRPORT.fullmatch(name) for name in (origin, destination)]
            if not all(airports):
                raise ValueError("Не распознаны коды аэропортов")
            key = tuple(airport[1] for airport in airports)
            if key in seen or key[0] == key[1] or not all(re.fullmatch(r"\d+(?:[,.]\d{1,2})?", value) for value in values):
                raise ValueError("Повтор направления или некорректная ставка")
            rates = [float(value.replace(",", ".")) for value in values]
            if min(rates) <= 0:
                raise ValueError("Нулевая ставка в авиатарифе")
            seen.add(key)
            rows.append([origin, destination, rates])
    if not rows:
        raise ValueError("Федеральные ставки Якутии не найдены")
    return {"vat": "included", "breakpoints_kg": breakpoints, "minimum_weight_kg": minimum,
            "actual_weight_step_kg": step, "cubic_cm_per_kg": divisor, "awb_rub": awb, "federal_rows": rows}


def refresh_air(directory, publish, *, now=None):
    checked = now or datetime.now(timezone.utc)
    html, charset = download(PAGE_URL, 2_000_000)
    listed = releases(html.decode(charset or "utf-8"))
    first_supported = date.fromisoformat(bundled_air_tariff()["valid_from"])
    eligible = [(start, end, url) for (start, end), url in sorted(listed.items()) if start >= first_supported]
    if not eligible:
        raise ValueError("На странице Якутии появился более старый выпуск")
    if len({start for start, _end, _url in eligible}) != len(eligible):
        raise ValueError("На странице Якутии несколько выпусков с одинаковой датой начала")
    published = None
    for start, end, url in eligible:
        previous_path = latest_air_snapshot(directory, start)
        previous = read_air_snapshot(previous_path) if previous_path else bundled_air_tariff()
        data, _charset = download(url, 8_000_000)
        tariff = parse_pdf(data)
        tariff.update(valid_from=start.isoformat(), valid_until=end.isoformat(), source_url=url,
                      observed_on=checked.astimezone(YAKUTSK).date().isoformat())
        tariff["version"] = version_of(tariff)
        if not previous_path or tariff["version"] != previous["version"] or url != previous["source_url"]:
            published = publish(directory, "yakutia", tariff["valid_from"], tariff, checked)
    return published
