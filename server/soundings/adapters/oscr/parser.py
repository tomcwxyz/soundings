"""Parse OSCR's public bulk ZIP without persisting contact or trustee details."""

import csv
import io
import math
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

MAX_CSV_BYTES = 200 * 1024 * 1024
CHARITY_NUMBER = re.compile(r"^SC[0-9]{6}$")
CURRENT_STATUSES = {"active", "not monitored", "not submitted"}


@dataclass(frozen=True)
class OscrRecord:
    number: str
    name: str
    status: str
    postcode: str
    main_location: str
    geographical_spread: str
    registration_date: str | None
    latest_income: float | None
    latest_expenditure: float | None
    purposes: tuple[str, ...]
    website: str
    regulatory_type: str


def _header(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.strip().lower())


def _money(value: str) -> float | None:
    cleaned = value.replace(",", "").replace("£", "").strip()
    if not cleaned:
        return None
    try:
        number = float(Decimal(cleaned))
    except (InvalidOperation, OverflowError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def parse_register_csv(content: bytes) -> list[OscrRecord]:
    try:
        data = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        data = content.decode("cp1252")
    reader = csv.DictReader(io.StringIO(data, newline=""))
    names = {_header(k): k for k in (reader.fieldnames or []) if k}
    if not {"charitynumber", "charityname", "charitystatus"}.issubset(names):
        raise ValueError("OSCR CSV missing number/name/status headers")

    def field(row: dict[str, str | None], *keys: str) -> str:
        for key in keys:
            col = names.get(_header(key))
            if col:
                val = (row.get(col) or "").strip()
                if val:
                    return val
        return ""

    results: dict[str, OscrRecord] = {}
    for raw in reader:
        number = field(raw, "Charity Number").upper()
        name = field(raw, "Charity Name")
        if not CHARITY_NUMBER.fullmatch(number) or not name:
            raise ValueError(f"OSCR row missing valid identity: {number!r}")
        if number in results:
            raise ValueError(f"Duplicate OSCR charity number: {number}")
        status = field(raw, "Charity Status")
        if status.casefold() not in CURRENT_STATUSES:
            raise ValueError(f"Unexpected current-register status {status!r} for {number}")
        date_input = field(raw, "Registration Date")
        date_iso = None
        if date_input:
            for date_format in ("%d/%m/%Y", "%Y-%m-%d"):
                try:
                    date_iso = datetime.strptime(date_input, date_format).date().isoformat()
                    break
                except ValueError:
                    continue
        purposes = field(raw, "Purposes")
        results[number] = OscrRecord(
            number=number,
            name=name,
            status=status,
            postcode=field(raw, "Postcode").replace(" ", "").upper(),
            main_location=field(raw, "Main Operating Location"),
            geographical_spread=field(raw, "Geographical Spread"),
            registration_date=date_iso,
            latest_income=_money(field(raw, "Income")),
            latest_expenditure=_money(field(raw, "Expenditure")),
            purposes=tuple(p.strip() for p in purposes.split(";") if p.strip()),
            website=field(raw, "Website"),
            regulatory_type=field(raw, "Regulatory Type"),
        )
    if not results:
        raise ValueError("OSCR register CSV contains no charities")
    return list(results.values())


def parse_register_zip(blob: bytes) -> list[OscrRecord]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise ValueError("OSCR download is not a ZIP archive") from exc
    with archive:
        candidates = [i for i in archive.infolist() if i.filename.lower().endswith(".csv")]
        if not candidates:
            raise ValueError("OSCR ZIP has no CSV file")
        parsed: list[list[OscrRecord]] = []
        for info in candidates:
            if info.file_size > MAX_CSV_BYTES:
                raise ValueError("OSCR CSV exceeds allowed uncompressed size")
            if info.file_size == 0:
                continue
            try:
                parsed.append(parse_register_csv(archive.read(info)))
            except ValueError as exc:
                if "missing number/name/status headers" not in str(exc):
                    raise
        if len(parsed) != 1:
            raise ValueError(f"Expected one OSCR register CSV, got {len(parsed)}")
        return parsed[0]
