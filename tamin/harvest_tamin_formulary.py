#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Harvest Tamin pharmacy formulary rows from the public DevExpress grid.

Modes:
  1. Offline: parse a saved HTML snapshot. This extracts only the rows that
     are present in the saved page.
  2. Online: run from a network that can access darman.tamin.ir and page
     through the ASPxGridView callback endpoint.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

try:
    from bs4 import BeautifulSoup
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: beautifulsoup4. Install it with: python3 -m pip install beautifulsoup4"
    ) from exc


DEFAULT_URL = "https://darman.tamin.ir/Forms/Public/Druglist.aspx?pagename=hdpDrugList"
GRID_CLIENT_ID = "ctl00_ContentPlaceHolder1_Grd_Dr"
GRID_UNIQUE_ID = "ctl00$ContentPlaceHolder1$Grd_Dr"
CALLBACK_STATE_NAME = "ctl00$ContentPlaceHolder1$Grd_Dr$CallbackState"
CALLBACK_STATE_ID = "ctl00_ContentPlaceHolder1_Grd_Dr_CallbackState"


FIELDNAMES = [
    "drug_code",
    "drug_name",
    "insurance_status",
    "insurance_status_normalized",
    "hospital_status",
    "hospital_status_normalized",
    "document_office_status",
    "document_office_status_normalized",
    "file_required_status",
    "file_required_normalized",
    "max_prescription",
    "hard_to_treat_status",
    "hard_to_treat_normalized",
    "barcode_required_status",
    "barcode_required_normalized",
    "electronic_prescription_only_status",
    "electronic_prescription_only_normalized",
    "price_without_subsidy",
    "organization_share_percent_without_subsidy",
    "government_subsidy",
    "accepted_total_price",
    "related_specialties_button",
    "unrelated_specialties_button",
    "source_page",
]


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = value.replace("\u200c", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def parse_int(value: str) -> Optional[int]:
    text = clean_text(value).replace(",", "")
    text = text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    if text == "" or not re.fullmatch(r"-?\d+", text):
        return None
    return int(text)


def parse_percent(value: str) -> Optional[float]:
    text = clean_text(value).replace("%", "").replace(",", ".")
    text = text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    if text == "" or not re.fullmatch(r"-?\d+(\.\d+)?", text):
        return None
    return float(text)


def normalize_flag(value: str) -> Optional[bool]:
    text = clean_text(value)
    text = text.replace("ي", "ی").replace("ك", "ک")
    if text in {"است", "بله", "دارد", "بلی", "yes", "true", "1"}:
        return True
    if text in {"نیست", "خير", "خیر", "ندارد", "no", "false", "0"}:
        return False
    return None


def normalize_insurance_status(value: str) -> str:
    text = clean_text(value).replace("ي", "ی").replace("ك", "ک")
    if text == "است":
        return "covered"
    if text in {"نیست", "خیر", "خير"}:
        return "not_covered"
    if "یارانه" in text:
        return "government_subsidy_only"
    return text


def input_has_button(td) -> bool:
    return bool(td.find("input"))


def extract_page_number(soup: BeautifulSoup, fallback: int = 1) -> int:
    text = soup.get_text(" ", strip=True)
    match = re.search(r"صفحه\s+([0-9۰-۹٠-٩]+)\s+از", text)
    if not match:
        return fallback
    return parse_int(match.group(1)) or fallback


def extract_page_count(soup: BeautifulSoup) -> Optional[int]:
    script_text = "\n".join(script.get_text("\n") for script in soup.find_all("script"))
    match = re.search(r"\bdxo\.pageCount\s*=\s*(\d+)", script_text)
    if match:
        return int(match.group(1))
    text = soup.get_text(" ", strip=True)
    match = re.search(r"صفحه\s+[0-9۰-۹٠-٩]+\s+از\s+([0-9۰-۹٠-٩]+)", text)
    if match:
        return parse_int(match.group(1))
    return None


def extract_total_rows(soup: BeautifulSoup) -> Optional[int]:
    text = soup.get_text(" ", strip=True)
    match = re.search(r"\(([0-9۰-۹٠-٩]+)\s+ردیف\)", text)
    if not match:
        return None
    return parse_int(match.group(1))


def parse_form_fields(soup: BeautifulSoup) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    for tag in soup.find_all(["input", "select", "textarea"]):
        name = tag.get("name")
        if not name:
            continue
        if tag.name == "select":
            selected = tag.find("option", selected=True)
            if selected is None:
                selected = tag.find("option")
            fields[name] = selected.get("value", selected.get_text()) if selected else ""
        elif tag.name == "textarea":
            fields[name] = tag.get_text()
        else:
            input_type = (tag.get("type") or "").lower()
            if input_type in {"submit", "button", "image", "file"}:
                continue
            if input_type in {"checkbox", "radio"} and not tag.has_attr("checked"):
                continue
            fields[name] = tag.get("value", "")
    fields.setdefault("__EVENTTARGET", "")
    fields.setdefault("__EVENTARGUMENT", "")
    fields.setdefault("__LASTFOCUS", "")
    return fields


def parse_rows(html_text: str, fallback_page: int = 1) -> Tuple[List[dict], Optional[int], Optional[int], Dict[str, str]]:
    soup = BeautifulSoup(html_text, "html.parser")
    page_number = extract_page_number(soup, fallback=fallback_page)
    page_count = extract_page_count(soup)
    total_rows = extract_total_rows(soup)
    records: List[dict] = []

    for tr in soup.find_all("tr", id=re.compile(r"_DXDataRow\d+$")):
        cells = tr.find_all("td", recursive=False)
        if len(cells) < 16:
            continue
        raw = [clean_text(cell.get_text(" ", strip=True)) for cell in cells]
        if not raw[0]:
            continue

        record = {
            "drug_code": raw[0],
            "drug_name": raw[1],
            "insurance_status": raw[2],
            "insurance_status_normalized": normalize_insurance_status(raw[2]),
            "hospital_status": raw[3],
            "hospital_status_normalized": normalize_flag(raw[3]),
            "document_office_status": raw[4],
            "document_office_status_normalized": normalize_flag(raw[4]),
            "file_required_status": raw[5],
            "file_required_normalized": normalize_flag(raw[5]),
            "max_prescription": raw[6],
            "hard_to_treat_status": raw[7],
            "hard_to_treat_normalized": normalize_flag(raw[7]),
            "barcode_required_status": raw[8],
            "barcode_required_normalized": normalize_flag(raw[8]),
            "electronic_prescription_only_status": raw[9],
            "electronic_prescription_only_normalized": normalize_flag(raw[9]),
            "price_without_subsidy": parse_int(raw[10]),
            "organization_share_percent_without_subsidy": parse_percent(raw[11]),
            "government_subsidy": parse_int(raw[12]),
            "accepted_total_price": parse_int(raw[13]),
            "related_specialties_button": input_has_button(cells[14]),
            "unrelated_specialties_button": input_has_button(cells[15]),
            "source_page": page_number,
        }
        records.append(record)

    return records, page_count, total_rows, parse_form_fields(soup)


def serialize_callback_args(items: List[str]) -> str:
    return "".join(f"{len(str(item))}|{item}" for item in items)


def grid_callback_param(page_index: int) -> str:
    # The visible pager calls doPagerOnClick("PN{zero_based_page_index}").
    serialized = serialize_callback_args(["PAGERONCLICK", f"PN{page_index}"])
    prepared = f"GB|{len(serialized)};{serialized};"
    return f"c0:{prepared}"


def decode_js_string(source: str, start_quote_index: int) -> Tuple[str, int]:
    quote = source[start_quote_index]
    if quote not in {"'", '"'}:
        raise ValueError("JS string does not start with a quote")
    i = start_quote_index + 1
    out: List[str] = []
    while i < len(source):
        ch = source[i]
        if ch == quote:
            return "".join(out), i + 1
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        i += 1
        if i >= len(source):
            break
        esc = source[i]
        if esc == "n":
            out.append("\n")
        elif esc == "r":
            out.append("\r")
        elif esc == "t":
            out.append("\t")
        elif esc == "b":
            out.append("\b")
        elif esc == "f":
            out.append("\f")
        elif esc == "u" and i + 4 < len(source):
            out.append(chr(int(source[i + 1 : i + 5], 16)))
            i += 4
        elif esc == "x" and i + 2 < len(source):
            out.append(chr(int(source[i + 1 : i + 3], 16)))
            i += 2
        else:
            out.append(esc)
        i += 1
    raise ValueError("Unterminated JS string in callback response")


class DevExpressCallbackError(RuntimeError):
    pass


def is_retryable_network_error(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return 500 <= exc.code < 600
    if isinstance(exc, (TimeoutError, socket.timeout, ssl.SSLError)):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (TimeoutError, socket.timeout, ssl.SSLError, OSError)):
            return True
        text = str(exc).lower()
        return any(
            marker in text
            for marker in [
                "timed out",
                "timeout",
                "connection reset",
                "connection aborted",
                "temporarily unavailable",
            ]
        )
    return False


def extract_js_property_string(text: str, property_name: str) -> Optional[str]:
    match = re.search(rf"['\"]?{re.escape(property_name)}['\"]?\s*:", text)
    if not match:
        return None
    i = match.end()
    while i < len(text) and text[i].isspace():
        i += 1
    if i >= len(text) or text[i] not in {"'", '"'}:
        return None
    return decode_js_string(text, i)[0]


def extract_callback_result(response_text: str) -> str:
    text = response_text.strip()
    prefix_index = text.find("/*DX*/")
    if prefix_index >= 0:
        text = text[prefix_index + len("/*DX*/") :]

    general_error = extract_js_property_string(text, "generalError")
    if general_error:
        raise DevExpressCallbackError(general_error)

    result = extract_js_property_string(text, "result")
    if result is None:
        raise ValueError(f"Could not find DevExpress result in callback response: {text[:200]!r}")
    return result


@dataclass
class OnlineHarvestConfig:
    url: str
    delay_seconds: float = 0.25
    timeout_seconds: int = 60
    max_retries: int = 5
    retry_delay_seconds: float = 2.0
    user_agent: str = "Mozilla/5.0 (compatible; TaminFormularyHarvester/1.0)"


class OnlineHarvester:
    def __init__(self, config: OnlineHarvestConfig):
        self.config = config
        self.cookies = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
        self.form_fields: Dict[str, str] = {}
        self.last_html: str = ""

    def _request(self, data: Optional[Dict[str, str]] = None) -> str:
        encoded = None
        parsed_url = urllib.parse.urlparse(self.config.url)
        headers = {
            "User-Agent": self.config.user_agent,
            "Referer": self.config.url,
            "Origin": f"{parsed_url.scheme}://{parsed_url.netloc}",
        }
        if data is not None:
            encoded = urllib.parse.urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        for attempt in range(self.config.max_retries + 1):
            request = urllib.request.Request(self.config.url, data=encoded, headers=headers)
            try:
                with self.opener.open(request, timeout=self.config.timeout_seconds) as response:
                    body = response.read()
                    charset = response.headers.get_content_charset() or "utf-8"
                return body.decode(charset, errors="replace")
            except Exception as exc:
                if not is_retryable_network_error(exc) or attempt >= self.config.max_retries:
                    raise
                print(
                    f"network error; retrying request ({attempt + 1}/{self.config.max_retries}): {exc}",
                    file=sys.stderr,
                )
                time.sleep(self.config.retry_delay_seconds)
        raise RuntimeError("unreachable request retry state")

    def load_initial(self, seed_html: Optional[str]) -> Tuple[List[dict], int, Optional[int]]:
        if seed_html is None:
            seed_html = self._request()
        self.last_html = seed_html
        rows, page_count, total_rows, fields = parse_rows(seed_html, fallback_page=1)
        self.form_fields = fields
        if not page_count:
            raise RuntimeError("Could not discover page count from the initial page.")
        return rows, page_count, total_rows

    def refresh_session(self) -> None:
        seed_html = self._request()
        _, page_count, _, fields = parse_rows(seed_html, fallback_page=1)
        if not page_count:
            raise RuntimeError("Could not refresh Tamin session/viewstate.")
        self.form_fields = fields
        self.last_html = seed_html

    def fetch_page(self, page_index: int) -> List[dict]:
        for attempt in range(self.config.max_retries + 1):
            data = dict(self.form_fields)
            data["__CALLBACKID"] = GRID_UNIQUE_ID
            data["__CALLBACKPARAM"] = grid_callback_param(page_index)
            response_text = self._request(data)
            try:
                result_html = extract_callback_result(response_text)
            except DevExpressCallbackError as exc:
                message = str(exc)
                retryable = "Invalid viewstate" in message or "viewstate" in message.lower()
                if retryable and attempt < self.config.max_retries:
                    print(
                        f"callback viewstate expired on page {page_index + 1}; refreshing session "
                        f"and retrying ({attempt + 1}/{self.config.max_retries})",
                        file=sys.stderr,
                    )
                    time.sleep(self.config.retry_delay_seconds)
                    self.refresh_session()
                    continue
                raise

            rows, _, _, fields = parse_rows(result_html, fallback_page=page_index + 1)
            if CALLBACK_STATE_NAME in fields:
                self.form_fields[CALLBACK_STATE_NAME] = fields[CALLBACK_STATE_NAME]
            elif CALLBACK_STATE_ID in result_html:
                soup = BeautifulSoup(result_html, "html.parser")
                state = soup.find(id=CALLBACK_STATE_ID)
                if state and state.get("value") is not None:
                    self.form_fields[CALLBACK_STATE_NAME] = state["value"]
            self.last_html = result_html
            return rows
        raise RuntimeError(f"Failed to fetch page {page_index + 1}")


def dedupe_records(records: Iterable[dict]) -> List[dict]:
    seen = set()
    result = []
    for record in records:
        key = (record.get("drug_code"), record.get("drug_name"), record.get("source_page"))
        if key in seen:
            continue
        seen.add(key)
        result.append(record)
    return result


def write_csv(path: Path, records: List[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def write_json(path: Path, records: List[dict], metadata: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"metadata": metadata, "records": records}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Harvest Tamin formulary rows from the public ASPxGridView page.")
    parser.add_argument("--input", type=Path, help="Saved HTML page to parse or use as the online seed page.")
    parser.add_argument("--url", default=DEFAULT_URL, help="Live formulary URL for online harvesting.")
    parser.add_argument("--online", action="store_true", help="Replay DevExpress callbacks to harvest all pages.")
    parser.add_argument("--pages", default="all", help="Online pages to harvest: all, 1-10, or comma list like 1,2,260.")
    parser.add_argument("--delay", type=float, default=0.25, help="Delay between online callback requests in seconds.")
    parser.add_argument("--timeout", type=int, default=60, help="Per-request network timeout in seconds.")
    parser.add_argument("--max-retries", type=int, default=5, help="Retries for retryable callback/session errors.")
    parser.add_argument("--retry-delay", type=float, default=2.0, help="Delay before retrying after a refreshable error.")
    parser.add_argument("--resume", action="store_true", help="Continue from an existing output JSON/CSV instead of starting over.")
    parser.add_argument("--csv", type=Path, help="Output CSV path.")
    parser.add_argument("--json", type=Path, help="Output JSON path.")
    parser.add_argument("--no-dedupe", action="store_true", help="Do not deduplicate records.")
    return parser.parse_args(argv)


def parse_page_selection(spec: str, page_count: int) -> List[int]:
    if spec == "all":
        return list(range(1, page_count + 1))
    pages = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start, end = int(start_s), int(end_s)
            pages.update(range(start, end + 1))
        else:
            pages.add(int(part))
    return [p for p in sorted(pages) if 1 <= p <= page_count]


def load_existing_harvest(args: argparse.Namespace) -> Tuple[List[dict], Optional[int]]:
    if args.json and args.json.exists():
        payload = json.loads(args.json.read_text(encoding="utf-8"))
        records = payload.get("records", [])
        metadata = payload.get("metadata", {})
        last_page = metadata.get("last_page_harvested")
        if last_page is None:
            last_page = max((parse_int(str(row.get("source_page", ""))) or 0 for row in records), default=0)
        return records, int(last_page or 0)

    if args.csv and args.csv.exists():
        with args.csv.open(encoding="utf-8-sig", newline="") as f:
            records = list(csv.DictReader(f))
        last_page = max((parse_int(str(row.get("source_page", ""))) or 0 for row in records), default=0)
        return records, int(last_page or 0)

    return [], None


def build_metadata(
    args: argparse.Namespace,
    page_count: Optional[int],
    total_rows: Optional[int],
    records: List[dict],
    completed: bool,
    last_page_harvested: Optional[int],
    error: Optional[str] = None,
) -> dict:
    metadata = {
        "source_url": args.url,
        "online": args.online,
        "input": str(args.input) if args.input else None,
        "page_count_detected": page_count,
        "total_rows_detected": total_rows,
        "records_extracted": len(records),
        "completed": completed,
        "last_page_harvested": last_page_harvested,
    }
    if error:
        metadata["error"] = error
    return metadata


def write_outputs_if_requested(args: argparse.Namespace, records: List[dict], metadata: dict) -> None:
    if args.csv:
        write_csv(args.csv, records)
    if args.json:
        write_json(args.json, records, metadata)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    seed_html = read_text(args.input) if args.input else None

    if args.online:
        harvester = OnlineHarvester(
            OnlineHarvestConfig(
                url=args.url,
                delay_seconds=args.delay,
                timeout_seconds=args.timeout,
                max_retries=args.max_retries,
                retry_delay_seconds=args.retry_delay,
            )
        )
        initial_rows, page_count, total_rows = harvester.load_initial(seed_html)
        selected_pages = parse_page_selection(args.pages, page_count)
        records: List[dict] = []
        last_page_harvested = None
        if args.resume:
            existing_records, existing_last_page = load_existing_harvest(args)
            if existing_records:
                records.extend(existing_records)
                last_page_harvested = existing_last_page
                selected_pages = [page for page in selected_pages if page > (existing_last_page or 0)]
                print(
                    f"resuming from page {existing_last_page}; loaded {len(existing_records)} existing rows",
                    file=sys.stderr,
                )
        if 1 in selected_pages:
            records.extend(initial_rows)
            last_page_harvested = 1
            checkpoint_records = dedupe_records(records) if not args.no_dedupe else list(records)
            write_outputs_if_requested(
                args,
                checkpoint_records,
                build_metadata(args, page_count, total_rows, checkpoint_records, False, last_page_harvested),
            )
        try:
            for page_number in selected_pages:
                if page_number == 1:
                    continue
                rows = harvester.fetch_page(page_number - 1)
                records.extend(rows)
                last_page_harvested = page_number
                checkpoint_records = dedupe_records(records) if not args.no_dedupe else list(records)
                write_outputs_if_requested(
                    args,
                    checkpoint_records,
                    build_metadata(args, page_count, total_rows, checkpoint_records, False, last_page_harvested),
                )
                print(f"harvested page {page_number}/{page_count}: {len(rows)} rows", file=sys.stderr)
                if args.delay:
                    time.sleep(args.delay)
        except Exception as exc:
            checkpoint_records = dedupe_records(records) if not args.no_dedupe else list(records)
            write_outputs_if_requested(
                args,
                checkpoint_records,
                build_metadata(
                    args,
                    page_count,
                    total_rows,
                    checkpoint_records,
                    False,
                    last_page_harvested,
                    error=str(exc),
                ),
            )
            raise
    else:
        if seed_html is None:
            raise SystemExit("Offline mode requires --input. Use --online to fetch the live page.")
        records, page_count, total_rows, _ = parse_rows(seed_html)
        last_page_harvested = extract_page_number(BeautifulSoup(seed_html, "html.parser"))

    if not args.no_dedupe:
        records = dedupe_records(records)

    metadata = build_metadata(args, page_count, total_rows, records, True, last_page_harvested)

    write_outputs_if_requested(args, records, metadata)
    if not args.csv and not args.json:
        print(json.dumps({"metadata": metadata, "records": records}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
