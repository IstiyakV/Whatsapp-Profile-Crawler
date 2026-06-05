import csv
import io
import re
from dataclasses import dataclass

from .config import get_settings


PHONE_COLUMNS = ["phone", "number", "mobile", "msisdn", "contact", "whatsapp"]


@dataclass
class ParsePreview:
    numbers: list[str]
    duplicates_removed: int
    invalid_rows: int
    detected_column: str | None
    preview: list[str]


def normalize_phone(raw: str) -> str | None:
    settings = get_settings()
    cleaned = re.sub(r"[\s().-]", "", str(raw or "").strip())
    if not cleaned:
        return None
    if cleaned.startswith("+"):
        candidate = "+" + re.sub(r"\D", "", cleaned)
    else:
        digits = re.sub(r"\D", "", cleaned)
        if not digits:
            return None
        if digits.startswith("00"):
            digits = digits[2:]
            candidate = f"+{digits}"
        elif digits.startswith("01") and len(digits) == 11 and settings.default_country_code == "+880":
            candidate = f"+88{digits}"
        elif digits.startswith(settings.default_country_code.replace("+", "")):
            candidate = f"+{digits}"
        else:
            candidate = f"{settings.default_country_code}{digits}"
    digits_only = candidate.replace("+", "")
    if len(digits_only) < 8 or len(digits_only) > 15:
        return None
    return candidate


def _dedupe(numbers: list[str]) -> tuple[list[str], int]:
    seen: set[str] = set()
    unique: list[str] = []
    duplicates = 0
    for number in numbers:
        if number in seen:
            duplicates += 1
            continue
        seen.add(number)
        unique.append(number)
    return unique, duplicates


def parse_txt(content: bytes) -> ParsePreview:
    text = content.decode("utf-8-sig", errors="ignore")
    parsed: list[str] = []
    invalid = 0
    for line in text.splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        phone = normalize_phone(value)
        if phone:
            parsed.append(phone)
        else:
            invalid += 1
    numbers, duplicates = _dedupe(parsed)
    return ParsePreview(numbers, duplicates, invalid, None, numbers[:10])


def parse_csv(content: bytes) -> ParsePreview:
    text = content.decode("utf-8-sig", errors="ignore")
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample) if sample.strip() else csv.excel
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = reader.fieldnames or []
    lower = {header.lower().strip(): header for header in headers}
    detected = next((lower[name] for name in PHONE_COLUMNS if name in lower), headers[0] if headers else None)
    parsed: list[str] = []
    invalid = 0
    for row in reader:
        raw = row.get(detected or "", "")
        phone = normalize_phone(raw)
        if phone:
            parsed.append(phone)
        else:
            invalid += 1
    numbers, duplicates = _dedupe(parsed)
    return ParsePreview(numbers, duplicates, invalid, detected, numbers[:10])


def parse_upload(filename: str, content: bytes) -> ParsePreview:
    name = filename.lower()
    if name.endswith(".csv"):
        return parse_csv(content)
    return parse_txt(content)
