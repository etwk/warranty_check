import argparse
import csv
import json
import logging
import re
import sys
import time
from datetime import datetime
from html import unescape
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# web url: https://newthink.lenovo.com.cn/deviceGuarantee.html?selname=AAAAAAAAA
PCSUPPORT_API_BASE = "https://pcsupport.lenovo.com/us/en/api/v4"
WARRANTY_API_BASE = "https://newthink.lenovo.com.cn/api/ThinkHome/Machine/WarrantyListInfo"
MACHINE_API_BASE = "https://newthink.lenovo.com.cn/api/ThinkHome/Machine/MachineListInfo"
CONFIG_API_BASE = "https://newthink.lenovo.com.cn/api/ThinkHome/Machine/ConfigListInfo"
API_TIMEOUT_SECONDS = 20
API_MAX_RETRIES = 2
API_RETRY_BACKOFF_SECONDS = 1
RETRYABLE_HTTP_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
NOT_FOUND_MESSAGE_PATTERNS = (
    "not found",
    "no information was found",
    "no information found",
    "no warranty",
    "not exist",
    "没有保修",
    "无保修",
    "不存在",
    "未找到",
    "没有信息",
)
NA_VALUE = 'N/A'
QUERY_STATUS_OK = 'OK'
QUERY_STATUS_PARTIAL = 'PARTIAL'
QUERY_STATUS_NOT_FOUND = 'NOT_FOUND'
QUERY_STATUS_INVALID_INPUT = 'INVALID_INPUT'
QUERY_STATUS_API_ERROR = 'API_ERROR'
NO_DATA_STATES = {'not_found', 'empty'}
CSV_HEADERS = [
    'Serial Number',
    'Model',
    'Warranty Start',
    'Warranty End',
    'MTM',
    'CPU',
    'RAM (Factory)',
    'Disk (Factory)',
    'Query Status',
    'Notes',
]
REQUIRED_OUTPUT_FIELDS = [
    'model',
    'start_date',
    'end_date',
    'mtm',
    'cpu',
    'ram_factory',
    'disk_factory',
]
ENDPOINT_STATE_FIELDS = [
    'warranty_state',
    'machine_info_state',
    'config_info_state',
]


def fetch_json(url, params=None, timeout=API_TIMEOUT_SECONDS, retries=API_MAX_RETRIES):
    """Fetch JSON from Lenovo APIs with retry for transient failures."""
    if params:
        url = f"{url}?{urlencode(params)}"

    for attempt in range(retries + 1):
        try:
            with urlopen(url, timeout=timeout) as response:
                payload = response.read().decode("utf-8")
            return json.loads(payload)
        except HTTPError as e:
            if e.code not in RETRYABLE_HTTP_STATUS_CODES or attempt == retries:
                raise
            wait_seconds = API_RETRY_BACKOFF_SECONDS * (2 ** attempt)
            logging.info(
                f"Transient Lenovo API HTTP {e.code} for {url}; retrying in {wait_seconds}s "
                f"({attempt + 1}/{retries})"
            )
            time.sleep(wait_seconds)
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
            if attempt == retries:
                raise
            wait_seconds = API_RETRY_BACKOFF_SECONDS * (2 ** attempt)
            logging.info(
                f"Transient Lenovo API error for {url}: {e}; retrying in {wait_seconds}s "
                f"({attempt + 1}/{retries})"
            )
            time.sleep(wait_seconds)


def fetch_json_request(request, timeout=API_TIMEOUT_SECONDS, retries=API_MAX_RETRIES):
    """Fetch JSON from a prepared urllib Request with retry for transient failures."""
    for attempt in range(retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = response.read().decode("utf-8")
            return json.loads(payload)
        except HTTPError as e:
            if e.code not in RETRYABLE_HTTP_STATUS_CODES or attempt == retries:
                raise
            wait_seconds = API_RETRY_BACKOFF_SECONDS * (2 ** attempt)
            logging.info(
                f"Transient Lenovo API HTTP {e.code} for {request.full_url}; retrying in "
                f"{wait_seconds}s ({attempt + 1}/{retries})"
            )
            time.sleep(wait_seconds)
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
            if attempt == retries:
                raise
            wait_seconds = API_RETRY_BACKOFF_SECONDS * (2 ** attempt)
            logging.info(
                f"Transient Lenovo API error for {request.full_url}: {e}; retrying in "
                f"{wait_seconds}s ({attempt + 1}/{retries})"
            )
            time.sleep(wait_seconds)


def configure_logging():
    """Enable clean real-time logging when the script is run from the terminal."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )


def get_warranty(sn):
    """Note: Lenovo API might not available from time to time"""
    return fetch_json(WARRANTY_API_BASE, params={"sn": sn})


def get_machine_info(sn):
    """Fetch device metadata such as product model."""
    return fetch_json(MACHINE_API_BASE, params={"sn": sn})


def get_config_info(sn):
    """Fetch device configuration details such as CPU and storage."""
    return fetch_json(CONFIG_API_BASE, params={"sn": sn})


def get_pcsupport_products(sn):
    """Resolve a serial number on Lenovo PC Support."""
    request = Request(
        f"{PCSUPPORT_API_BASE}/mse/getproducts?{urlencode({'productId': sn})}",
        headers={
            "referer": "https://pcsupport.lenovo.com/us/en/warranty-lookup",
            "x-requested-with": "XMLHttpRequest",
        },
    )
    return fetch_json_request(request)


def get_pcsupport_ibase_info(sn, machine_type, country="us", language="en"):
    """Fetch warranty and specification data from Lenovo PC Support."""
    payload = json.dumps({
        "serialNumber": sn,
        "machineType": machine_type,
        "country": country,
        "language": language,
    }).encode("utf-8")
    request = Request(
        f"{PCSUPPORT_API_BASE}/upsell/redport/getIbaseInfo",
        data=payload,
        headers={
            "content-type": "application/json",
            "origin": "https://pcsupport.lenovo.com",
            "referer": "https://pcsupport.lenovo.com/us/en/warranty-lookup",
            "x-requested-with": "XMLHttpRequest",
        },
    )
    return fetch_json_request(request)


def get_config_value(config_info, field_name):
    """Return a config value from Lenovo ConfigListInfo by its display field name."""
    for section in config_info or []:
        for item in section.get('data', []):
            material_name = item.get('material_name', '')
            normalized_name = material_name.split('.', 1)[-1]
            if normalized_name == field_name:
                return item.get('material_notes')
    return None


def append_issue(existing_issue, new_issue):
    """Append a note string without duplicating the same issue text."""
    if not new_issue:
        return existing_issue
    if not existing_issue:
        return new_issue
    existing_parts = existing_issue.split('; ')
    if new_issue in existing_parts:
        return existing_issue
    return f"{existing_issue}; {new_issue}"


def combine_issues(*issues):
    """Combine one or more semicolon-delimited issue strings without duplicates."""
    combined_issue = None
    for issue in issues:
        if not issue:
            continue
        for part in issue.split('; '):
            combined_issue = append_issue(combined_issue, part)
    return combined_issue


def merge_record_values(data, values):
    """Fill output fields only when they are currently missing."""
    for key, value in values.items():
        if value and not data.get(key):
            data[key] = value


def message_indicates_not_found(message):
    """Return True when an API message clearly indicates no matching data exists."""
    normalized_message = (message or "").strip().lower()
    return any(pattern in normalized_message for pattern in NOT_FOUND_MESSAGE_PATTERNS)


def normalize_serial_number(sn):
    """Normalize user input into the Lenovo serial number format used for queries."""
    return re.sub(r'[\s-]+', '', sn.strip()).upper()


def validate_serial_number(sn):
    """Conservative local validation before querying Lenovo APIs."""
    if not sn:
        return "Local validation failed: serial number is empty after normalization"
    if not sn.isalnum():
        return "Local validation failed: serial number must be alphanumeric after normalization"
    return None


def is_complete_record(warranty):
    """Return True when all CSV output fields have values."""
    return all(warranty.get(field) for field in REQUIRED_OUTPUT_FIELDS)


def build_notes(warranty):
    """Join current validation/API issues into one CSV-friendly note string."""
    notes = [
        warranty.get('validation_error'),
        warranty.get('warranty_issue'),
        warranty.get('machine_info_issue'),
        warranty.get('config_info_issue'),
    ]
    if warranty.get('backup_used'):
        notes.append("Used secondary API (pcsupport)")
    return combine_issues(*notes) or ""


def get_query_status(warranty):
    """Summarize the final result for one serial number."""
    if warranty.get('validation_error'):
        return QUERY_STATUS_INVALID_INPUT
    if is_complete_record(warranty):
        return QUERY_STATUS_OK

    endpoint_states = [warranty.get(field, 'pending') for field in ENDPOINT_STATE_FIELDS]
    any_values = any(warranty.get(field) for field in REQUIRED_OUTPUT_FIELDS)

    if any_values:
        return QUERY_STATUS_PARTIAL
    if all(state in NO_DATA_STATES for state in endpoint_states):
        return QUERY_STATUS_NOT_FOUND
    if 'error' in endpoint_states or 'pending' in endpoint_states:
        return QUERY_STATUS_API_ERROR
    return QUERY_STATUS_PARTIAL


def to_csv_value(value):
    """Render a value for CSV output using N/A for missing fields."""
    return value if value else NA_VALUE


def assess_group_fields(data, field_names, label):
    """Return a generic result state/message for a group of output fields."""
    has_all = all(data.get(field) for field in field_names)
    has_any = any(data.get(field) for field in field_names)
    if has_all:
        return 'success', None
    if has_any:
        return 'partial', f"{label} data incomplete"
    return 'not_found', f"{label} data not found"


def strip_html(value):
    """Convert a small HTML fragment into readable plain text."""
    if not value:
        return ""
    text = re.sub(r"(?i)<br\s*/?>", " / ", value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return " ".join(text.split()).strip()


def parse_specification_table(specification_html):
    """Parse Lenovo PC Support specification HTML into a dict of row label -> value."""
    specs = {}
    if not specification_html:
        return specs

    rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", specification_html, flags=re.IGNORECASE | re.DOTALL)
    for row in rows:
        cells = re.findall(r"<td\b[^>]*>(.*?)</td>", row, flags=re.IGNORECASE | re.DOTALL)
        if len(cells) < 2:
            continue
        label = strip_html(cells[0]).lower()
        values = [strip_html(cell) for cell in cells[1:]]
        values = [value for value in values if value]
        if values:
            specs[label] = " | ".join(dict.fromkeys(values))
    return specs


def get_spec_value(specs, *labels):
    """Return the first available specification value for any candidate label."""
    for label in labels:
        value = specs.get(label.lower())
        if value:
            return value
    return None


def simplify_cpu_value(cpu_value):
    """Trim overly verbose processor strings when Lenovo includes a short model in parentheses."""
    if not cpu_value:
        return None
    match = re.search(r"\(([^()]+)\)\s*$", cpu_value)
    if match:
        return match.group(1)
    return cpu_value


def normalize_warranty_date(date_value):
    """Return a validated warranty date string or None when Lenovo returns malformed data."""
    if not isinstance(date_value, str):
        return None
    try:
        datetime.strptime(date_value, "%Y-%m-%d")
    except ValueError:
        return None
    return date_value


def extract_newthink_warranty_values(raw):
    """Extract normalized warranty start/end dates from a newthink warranty payload."""
    start_date = None
    end_date = None
    issue = None
    if not raw:
        return {
            "start_date": None,
            "end_date": None,
            "issue": None,
        }

    try:
        detail_data = raw.get('detail_data') or {}
        warranty_data = detail_data.get('onsite_data') or detail_data.get('warranty_data') or []

        for warranty in warranty_data:
            candidate_start = warranty.get('start_date')
            candidate_end = warranty.get('end_date')
            if candidate_start:
                normalized_start = normalize_warranty_date(candidate_start)
                if normalized_start:
                    start_date = (
                        compare_date(start_date, normalized_start, 'start')
                        if start_date else normalized_start
                    )
                else:
                    issue = append_issue(issue, f"Invalid warranty start date: {candidate_start}")
            if candidate_end:
                normalized_end = normalize_warranty_date(candidate_end)
                if normalized_end:
                    end_date = (
                        compare_date(end_date, normalized_end, 'end')
                        if end_date else normalized_end
                    )
                else:
                    issue = append_issue(issue, f"Invalid warranty end date: {candidate_end}")
    except Exception as e:
        issue = append_issue(issue, f"Warranty processing error: {e}")

    return {
        "start_date": start_date,
        "end_date": end_date,
        "issue": issue,
    }


def get_newthink_machine_values(machine_info):
    """Extract normalized model and MTM values from newthink machine info."""
    machine_info = machine_info or {}
    return {
        "model": machine_info.get('product_model'),
        "mtm": machine_info.get('machine_mtm'),
    }


def get_newthink_config_values(config_info):
    """Extract normalized CPU/RAM/Disk values from newthink config info."""
    config_info = config_info or []
    return {
        "cpu": get_config_value(config_info, 'CPU型号'),
        "ram_factory": get_config_value(config_info, '内存容量'),
        "disk_factory": get_config_value(config_info, '硬盘容量'),
    }


def refresh_primary_values(data):
    """Populate output fields from already-fetched newthink payloads."""
    merge_record_values(data, get_newthink_machine_values(data.get('machine_info')))
    merge_record_values(data, get_newthink_config_values(data.get('config_info')))
    warranty_values = extract_newthink_warranty_values(data.get('raw'))
    merge_record_values(
        data,
        {
            "start_date": warranty_values["start_date"],
            "end_date": warranty_values["end_date"],
        },
    )
    if warranty_values["issue"]:
        data['warranty_issue'] = append_issue(data.get('warranty_issue'), warranty_values["issue"])


def finalize_combined_group(data, field_names, state_field, issue_field, label):
    """Resolve the final group state after primary and supplemental lookups."""
    assessed_state, assessed_issue = assess_group_fields(data, field_names, label)
    current_state = data.get(state_field, 'pending')
    current_issue = data.get(issue_field)
    backup_state = data.get('pcsupport_state', 'pending')
    backup_issue = data.get('pcsupport_issue')

    if assessed_state == 'success':
        data[state_field] = 'success'
        data[issue_field] = None
        return

    if assessed_state == 'partial':
        data[state_field] = 'partial'
        final_issue = combine_issues(
            assessed_issue,
            current_issue if current_state in {'partial', 'error'} else None,
            f"{label} lookup error: {backup_issue}" if backup_state == 'error' and backup_issue else None,
        )
        data[issue_field] = final_issue
        return

    if current_state == 'error' or backup_state == 'error':
        data[state_field] = 'error'
        final_issue = combine_issues(
            current_issue,
            f"{label} lookup error: {backup_issue}" if backup_state == 'error' and backup_issue else None,
        )
        data[issue_field] = final_issue or f"{label} lookup error"
        return

    data[state_field] = 'not_found'
    data[issue_field] = assessed_issue


def get_pcsupport_machine_type(product_record):
    """Extract machine type from the Lenovo PC Support product id path."""
    product_id = product_record.get("Id", "")
    parts = product_id.split("/")
    if len(parts) >= 3:
        return parts[-3]
    return None


def get_pcsupport_mtm(product_record, machine_info):
    """Extract MTM from Lenovo PC Support data."""
    if machine_info.get("product"):
        return machine_info["product"]
    product_id = product_record.get("Id", "")
    parts = product_id.split("/")
    if len(parts) >= 2:
        return parts[-2]
    return None


def get_pcsupport_warranty_period(ibase_data):
    """Return the earliest start and latest end across Lenovo PC Support base warranties."""
    start_date = None
    end_date = None
    base_warranties = ibase_data.get("baseWarranties") or []
    if not base_warranties and ibase_data.get("currentWarranty"):
        base_warranties = [ibase_data["currentWarranty"]]

    for warranty in base_warranties:
        candidate_start = normalize_warranty_date(warranty.get("startDate"))
        candidate_end = normalize_warranty_date(warranty.get("endDate"))
        if candidate_start:
            start_date = (
                compare_date(start_date, candidate_start, "start")
                if start_date else candidate_start
            )
        if candidate_end:
            end_date = (
                compare_date(end_date, candidate_end, "end")
                if end_date else candidate_end
            )

    return start_date, end_date


def get_pcsupport_device_info(sn):
    """Return normalized warranty/device data from Lenovo PC Support."""
    products = get_pcsupport_products(sn)
    if not products:
        return {
            "state": "not_found",
            "issue": "No matching serial was found",
            "values": {},
        }

    product_record = products[0]
    machine_type = get_pcsupport_machine_type(product_record)
    if not machine_type:
        return {
            "state": "error",
            "issue": "Machine type was missing in the supplemental lookup response",
            "values": {},
        }

    ibase_response = get_pcsupport_ibase_info(sn, machine_type)
    if ibase_response.get("code") != 0 or not ibase_response.get("data"):
        description = ibase_response.get("msg", {}).get("desc") or "No information was found"
        response_state = "not_found" if message_indicates_not_found(description) else "error"
        return {
            "state": response_state,
            "issue": description,
            "values": {},
        }

    ibase_data = ibase_response["data"]
    machine_info = ibase_data.get("machineInfo") or {}
    specs = parse_specification_table(machine_info.get("specification"))
    start_date, end_date = get_pcsupport_warranty_period(ibase_data)
    values = {
        "model": machine_info.get("productName") or product_record.get("Name"),
        "mtm": get_pcsupport_mtm(product_record, machine_info),
        "start_date": start_date,
        "end_date": end_date,
        "cpu": simplify_cpu_value(get_spec_value(specs, "Processor", "CPU")),
        "ram_factory": get_spec_value(specs, "Memory", "RAM", "DRAM"),
        "disk_factory": get_spec_value(specs, "Hard Drive", "Storage", "Drive", "Disk"),
    }

    return {
        "state": "success" if any(values.values()) else "empty",
        "issue": None if any(values.values()) else "No supplemental device data was returned",
        "values": values,
    }


def needs_warranty_lookup(data):
    """Return True when warranty start/end are still incomplete."""
    return not all(data.get(field) for field in ('start_date', 'end_date'))


def needs_machine_lookup(data):
    """Return True when model/MTM are still incomplete."""
    return not all(data.get(field) for field in ('model', 'mtm'))


def needs_config_lookup(data):
    """Return True when CPU/RAM/Disk are still incomplete."""
    return not all(data.get(field) for field in ('cpu', 'ram_factory', 'disk_factory'))


def apply_newthink_warranty_response(data, warranty_response):
    """Store primary warranty response data and populate normalized date fields."""
    data['raw_fetched'] = True
    status_code = warranty_response.get('statusCode')
    message = warranty_response.get('message', {}).get('info')

    if status_code != 200:
        data['raw'] = None
        if message_indicates_not_found(message):
            data['warranty_state'] = 'not_found'
            data['warranty_issue'] = "Warranty data not found"
        else:
            data['warranty_state'] = 'error'
            data['warranty_issue'] = f"Warranty lookup returned status {status_code}: {message or 'unknown error'}"
        return

    raw = warranty_response.get('data')
    data['raw'] = raw
    if not raw:
        data['warranty_state'] = 'not_found'
        data['warranty_issue'] = "Warranty data not found"
        return

    warranty_values = extract_newthink_warranty_values(raw)
    merge_record_values(
        data,
        {
            'start_date': warranty_values['start_date'],
            'end_date': warranty_values['end_date'],
        },
    )
    assessed_state, assessed_issue = assess_group_fields(data, ('start_date', 'end_date'), 'Warranty')
    data['warranty_state'] = assessed_state
    data['warranty_issue'] = append_issue(assessed_issue, warranty_values['issue'])


def apply_newthink_machine_info_response(data, machine_response):
    """Store primary machine info response data and populate normalized model/MTM fields."""
    data['machine_info_fetched'] = True
    status_code = machine_response.get('statusCode')
    message = machine_response.get('message', {}).get('info')

    if status_code != 200:
        data['machine_info'] = {}
        if message_indicates_not_found(message):
            data['machine_info_state'] = 'not_found'
            data['machine_info_issue'] = "Model/MTM data not found"
        else:
            data['machine_info_state'] = 'error'
            data['machine_info_issue'] = (
                f"Model/MTM lookup returned status {status_code}: {message or 'unknown error'}"
            )
        return

    machine_info = machine_response.get('data', {}).get('data') or {}
    data['machine_info'] = machine_info
    merge_record_values(data, get_newthink_machine_values(machine_info))
    assessed_state, assessed_issue = assess_group_fields(data, ('model', 'mtm'), 'Model/MTM')
    data['machine_info_state'] = assessed_state
    data['machine_info_issue'] = assessed_issue


def apply_newthink_config_response(data, config_response):
    """Store primary config response data and populate normalized CPU/RAM/Disk fields."""
    data['config_info_fetched'] = True
    status_code = config_response.get('statusCode')
    message = config_response.get('message', {}).get('info')

    if status_code != 200:
        data['config_info'] = []
        if message_indicates_not_found(message):
            data['config_info_state'] = 'not_found'
            data['config_info_issue'] = "Configuration data not found"
        else:
            data['config_info_state'] = 'error'
            data['config_info_issue'] = (
                f"Configuration lookup returned status {status_code}: {message or 'unknown error'}"
            )
        return

    config_info = config_response.get('data') or []
    data['config_info'] = config_info
    merge_record_values(data, get_newthink_config_values(config_info))
    assessed_state, assessed_issue = assess_group_fields(
        data,
        ('cpu', 'ram_factory', 'disk_factory'),
        'Configuration',
    )
    data['config_info_state'] = assessed_state
    data['config_info_issue'] = assessed_issue


def compare_date(d1, d2, order):
    """
    Compare date string d1 and d2, choose the one by the order.
    
    Date example: '2023-01-03'
    order: start/end
    """
    d1_obj = datetime.strptime(d1, "%Y-%m-%d")
    d2_obj = datetime.strptime(d2, "%Y-%m-%d")
    if d1_obj < d2_obj:
        if order == 'start':
            choose = d1
        else:
            choose = d2
    else:
        if order == 'start':
            choose = d2
        else:
            choose = d1
    return choose


def initialize_record_defaults(data):
    """Populate all expected record keys so one serial can be processed end-to-end."""
    data.setdefault('pcsupport_fetched', False)
    data.setdefault('pcsupport_state', 'pending')
    data.setdefault('pcsupport_issue', None)
    data.setdefault('backup_used', False)
    data.setdefault('raw_fetched', 'raw' in data)
    data.setdefault('machine_info_fetched', 'machine_info' in data)
    data.setdefault('config_info_fetched', 'config_info' in data)
    data.setdefault('warranty_state', 'success' if data.get('raw') else 'pending')
    data.setdefault('machine_info_state', 'success' if data.get('machine_info') else 'pending')
    data.setdefault('config_info_state', 'success' if data.get('config_info') else 'pending')
    data.setdefault('validation_error', None)
    data.setdefault('warranty_issue', None)
    data.setdefault('machine_info_issue', None)
    data.setdefault('config_info_issue', None)


def finalize_record(data):
    """Finalize one serial number record after all lookup attempts."""
    if data.get('validation_error'):
        data['query_status'] = get_query_status(data)
        data['notes'] = build_notes(data)
        return

    refresh_primary_values(data)
    finalize_combined_group(data, ('start_date', 'end_date'), 'warranty_state', 'warranty_issue', 'Warranty')
    finalize_combined_group(data, ('model', 'mtm'), 'machine_info_state', 'machine_info_issue', 'Model/MTM')
    finalize_combined_group(
        data,
        ('cpu', 'ram_factory', 'disk_factory'),
        'config_info_state',
        'config_info_issue',
        'Configuration',
    )
    data['query_status'] = get_query_status(data)
    data['notes'] = build_notes(data)


def get_record_log_level(data):
    """Choose the log level for a per-serial summary line."""
    status = data.get('query_status') or get_query_status(data)
    if status == QUERY_STATUS_API_ERROR:
        return logging.ERROR
    if status in {QUERY_STATUS_INVALID_INPUT, QUERY_STATUS_PARTIAL} or data.get('backup_used'):
        return logging.WARNING
    return logging.INFO


def log_record_summary(index, total, sn, data):
    """Emit one summary line for the serial number unless retries emitted extra logs."""
    status = data.get('query_status') or get_query_status(data)
    parts = [
        f"[{index}/{total}] {sn}",
        f"status={status}",
        f"backup={'Y' if data.get('backup_used') else 'N'}",
    ]

    if data.get('model'):
        parts.append(f"model={data['model']}")
    if data.get('mtm'):
        parts.append(f"mtm={data['mtm']}")
    if data.get('start_date') or data.get('end_date'):
        parts.append(
            "warranty="
            f"{to_csv_value(data.get('start_date'))}->{to_csv_value(data.get('end_date'))}"
        )
    if data.get('notes') and (status != QUERY_STATUS_OK or data.get('backup_used')):
        parts.append(f"notes={data['notes']}")

    logging.log(get_record_log_level(data), " | ".join(parts))
    
class LenovoWarranty():
    def __init__(self, sn_file = None):
        self.collection = {}
        if sn_file:
            self.add(sn_file)
    
    def update_collection(self, sn_file):
        """Add new SN item to collection if not already exist""" 
        with open(sn_file, 'r') as file:
            for line in file:
                sn = normalize_serial_number(line)
                if sn and sn not in self.collection:
                    validation_error = validate_serial_number(sn)
                    self.collection[sn] = {
                        'pcsupport_fetched': bool(validation_error),
                        'pcsupport_state': 'invalid_input' if validation_error else 'pending',
                        'pcsupport_issue': None,
                        'backup_used': False,
                        'raw_fetched': bool(validation_error),
                        'machine_info_fetched': bool(validation_error),
                        'config_info_fetched': bool(validation_error),
                        'warranty_state': 'invalid_input' if validation_error else 'pending',
                        'machine_info_state': 'invalid_input' if validation_error else 'pending',
                        'config_info_state': 'invalid_input' if validation_error else 'pending',
                        'validation_error': validation_error,
                        'warranty_issue': None,
                        'machine_info_issue': None,
                        'config_info_issue': None,
                    }

    def fetch_warranty(self):
        """Fetch newthink data first, then use Lenovo PC Support to fill remaining gaps."""
        total = len(self.collection)
        for index, (sn, data) in enumerate(self.collection.items(), start=1):
            initialize_record_defaults(data)

            if data.get('validation_error'):
                finalize_record(data)
                log_record_summary(index, total, sn, data)
                continue

            refresh_primary_values(data)

            if needs_warranty_lookup(data) and not data['raw_fetched']:
                try:
                    warranty_response = get_warranty(sn)
                except Exception as e:
                    data['warranty_state'] = 'error'
                    data['warranty_issue'] = f"Warranty lookup error: {e}"
                else:
                    apply_newthink_warranty_response(data, warranty_response)

            if needs_machine_lookup(data) and not data['machine_info_fetched']:
                try:
                    machine_response = get_machine_info(sn)
                except Exception as e:
                    data['machine_info_state'] = 'error'
                    data['machine_info_issue'] = f"Model/MTM lookup error: {e}"
                else:
                    apply_newthink_machine_info_response(data, machine_response)

            if needs_config_lookup(data) and not data['config_info_fetched']:
                try:
                    config_response = get_config_info(sn)
                except Exception as e:
                    data['config_info_state'] = 'error'
                    data['config_info_issue'] = f"Configuration lookup error: {e}"
                else:
                    apply_newthink_config_response(data, config_response)

            refresh_primary_values(data)

            if not data['pcsupport_fetched'] and not is_complete_record(data):
                data['backup_used'] = True

                try:
                    pcsupport_result = get_pcsupport_device_info(sn)
                except Exception as e:
                    data['pcsupport_state'] = 'error'
                    data['pcsupport_issue'] = str(e)
                else:
                    data['pcsupport_fetched'] = pcsupport_result['state'] != 'error'
                    data['pcsupport_state'] = pcsupport_result['state']
                    data['pcsupport_issue'] = pcsupport_result['issue']
                    merge_record_values(data, pcsupport_result['values'])
            finalize_record(data)
            log_record_summary(index, total, sn, data)

    def process_warranty(self):
        """Finalize output fields and aggregate status after all lookup attempts."""
        for _, data in self.collection.items():
            finalize_record(data)

    def add(self, sn_file = None):
        """
        Add SN from file to collection, fetch and update warranty data.
        If no SN file provided, fetch and update existing
        """
        if sn_file:
            self.update_collection(sn_file)
            logging.info(f"Loaded {len(self.collection)} unique normalized serial numbers")
        self.fetch_warranty()
        self.status()

    def status(self):
        counts = self.get_status_counts()
        logging.info(
            "SN query status: "
            f"OK={counts[QUERY_STATUS_OK]}, "
            f"PARTIAL={counts[QUERY_STATUS_PARTIAL]}, "
            f"NOT_FOUND={counts[QUERY_STATUS_NOT_FOUND]}, "
            f"INVALID_INPUT={counts[QUERY_STATUS_INVALID_INPUT]}, "
            f"API_ERROR={counts[QUERY_STATUS_API_ERROR]}"
        )
        return counts

    def get_status_counts(self):
        counts = {
            QUERY_STATUS_OK: 0,
            QUERY_STATUS_PARTIAL: 0,
            QUERY_STATUS_NOT_FOUND: 0,
            QUERY_STATUS_INVALID_INPUT: 0,
            QUERY_STATUS_API_ERROR: 0,
        }
        for _, data in self.collection.items():
            counts[get_query_status(data)] += 1
        return counts

    def save(self, file = "lenovo_warranty.csv"):
        """Save warranty data to CSV file"""
        data = [CSV_HEADERS]
        for sn, warranty in self.collection.items():
            data.append([
                sn,
                to_csv_value(warranty.get('model')),
                to_csv_value(warranty.get('start_date')),
                to_csv_value(warranty.get('end_date')),
                to_csv_value(warranty.get('mtm')),
                to_csv_value(warranty.get('cpu')),
                to_csv_value(warranty.get('ram_factory')),
                to_csv_value(warranty.get('disk_factory')),
                get_query_status(warranty),
                warranty.get('notes', build_notes(warranty)),
            ])

        # write to csv
        with open(file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerows(data)

        logging.info(f"Saved warranty data to CSV file '{file}'")
        counts = self.get_status_counts()
        backup_used = sum(1 for warranty in self.collection.values() if warranty.get('backup_used'))
        logging.info(
            "Final summary: "
            f"total={len(self.collection)}, "
            f"OK={counts[QUERY_STATUS_OK]}, "
            f"PARTIAL={counts[QUERY_STATUS_PARTIAL]}, "
            f"NOT_FOUND={counts[QUERY_STATUS_NOT_FOUND]}, "
            f"INVALID_INPUT={counts[QUERY_STATUS_INVALID_INPUT]}, "
            f"API_ERROR={counts[QUERY_STATUS_API_ERROR]}, "
            f"backup_used={backup_used}, "
            f"output='{file}'"
        )

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch Lenovo warranty information and export it to CSV.")
    
    # Adding arguments
    parser.add_argument('-sn', type=str, required=True, help='Path to serial number file (one SN per line)')
    parser.add_argument('-csv', type=str, required=True, help='Path to the CSV file')
    args = parser.parse_args()
    
    # running
    configure_logging()
    warranty = LenovoWarranty()
    warranty.add(args.sn)
    warranty.save(args.csv)
    
