import argparse
import csv
import json
import logging
import re
import time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

# web url: https://newthink.lenovo.com.cn/deviceGuarantee.html?selname=AAAAAAAAA
WARRANTY_API_BASE = "https://newthink.lenovo.com.cn/api/ThinkHome/Machine/WarrantyListInfo"
MACHINE_API_BASE = "https://newthink.lenovo.com.cn/api/ThinkHome/Machine/MachineListInfo"
CONFIG_API_BASE = "https://newthink.lenovo.com.cn/api/ThinkHome/Machine/ConfigListInfo"
API_TIMEOUT_SECONDS = 20
API_MAX_RETRIES = 2
API_RETRY_BACKOFF_SECONDS = 1
RETRYABLE_HTTP_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
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


def get_warranty(sn):
    """Note: Lenovo API might not available from time to time"""
    return fetch_json(WARRANTY_API_BASE, params={"sn": sn})


def get_machine_info(sn):
    """Fetch device metadata such as product model."""
    return fetch_json(MACHINE_API_BASE, params={"sn": sn})


def get_config_info(sn):
    """Fetch device configuration details such as CPU and storage."""
    return fetch_json(CONFIG_API_BASE, params={"sn": sn})


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
    return '; '.join(note for note in notes if note)


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


def normalize_warranty_date(date_value):
    """Return a validated warranty date string or None when Lenovo returns malformed data."""
    if not isinstance(date_value, str):
        return None
    try:
        datetime.strptime(date_value, "%Y-%m-%d")
    except ValueError:
        return None
    return date_value


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
        """Fetch warranty and device metadata, then update collection."""
        succeed = 0
        total = 0
        for sn, data in self.collection.items():
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

            if data.get('validation_error'):
                logging.info(f"Skipping Lenovo queries for {sn}: {data['validation_error']}")
                continue

            # skip if required raw data for the sn already exists
            if (
                data['raw_fetched']
                and data['machine_info_fetched']
                and data['config_info_fetched']
            ):
                continue

            total += 1

            if not data['raw_fetched']:
                try:
                    warranty_response = get_warranty(sn)
                except Exception as e:
                    data['warranty_state'] = 'error'
                    data['warranty_issue'] = f"Warranty API error: {e}"
                    logging.info(f"Error while retrieving warranty data for {sn}: {e}")
                else:
                    data['raw_fetched'] = True
                    if warranty_response['statusCode'] != 200:
                        message = warranty_response.get('message', {}).get('info')
                        data['warranty_state'] = 'not_found'
                        data['warranty_issue'] = (
                            f"Warranty API returned status {warranty_response['statusCode']}: {message}"
                        )
                        data['raw'] = None
                        logging.info(
                            f"Warranty API returned status {warranty_response['statusCode']} for {sn}: {message}"
                        )
                    else:
                        raw = warranty_response.get('data')
                        data['raw'] = raw
                        if raw:
                            data['warranty_state'] = 'success'
                            data['warranty_issue'] = None
                        else:
                            data['warranty_state'] = 'empty'
                            data['warranty_issue'] = "Warranty API returned no usable data"
                            logging.info(f"No warranty data returned for SN: {sn}")

            if not data['machine_info_fetched']:
                try:
                    machine_response = get_machine_info(sn)
                except Exception as e:
                    data['machine_info_state'] = 'error'
                    data['machine_info_issue'] = f"Machine info API error: {e}"
                    logging.info(f"Error while retrieving machine info for {sn}: {e}")
                else:
                    data['machine_info_fetched'] = True
                    if machine_response['statusCode'] != 200:
                        message = machine_response.get('message', {}).get('info')
                        data['machine_info_state'] = 'not_found'
                        data['machine_info_issue'] = (
                            f"Machine info API returned status {machine_response['statusCode']}: {message}"
                        )
                        data['machine_info'] = {}
                        logging.info(
                            f"Machine info API returned status {machine_response['statusCode']} for {sn}: {message}"
                        )
                    else:
                        machine_info = machine_response.get('data', {}).get('data')
                        data['machine_info'] = machine_info or {}
                        if machine_info:
                            data['machine_info_state'] = 'success'
                            data['machine_info_issue'] = None
                        else:
                            data['machine_info_state'] = 'empty'
                            data['machine_info_issue'] = "Machine info API returned no usable data"
                            logging.info(f"No machine info returned for SN: {sn}")

            if not data['config_info_fetched']:
                try:
                    config_response = get_config_info(sn)
                except Exception as e:
                    data['config_info_state'] = 'error'
                    data['config_info_issue'] = f"Config info API error: {e}"
                    logging.info(f"Error while retrieving config info for {sn}: {e}")
                else:
                    data['config_info_fetched'] = True
                    if config_response['statusCode'] != 200:
                        message = config_response.get('message', {}).get('info')
                        data['config_info_state'] = 'not_found'
                        data['config_info_issue'] = (
                            f"Config info API returned status {config_response['statusCode']}: {message}"
                        )
                        data['config_info'] = []
                        logging.info(
                            f"Config info API returned status {config_response['statusCode']} for {sn}: {message}"
                        )
                    else:
                        config_info = config_response.get('data') or []
                        data['config_info'] = config_info
                        if config_info:
                            data['config_info_state'] = 'success'
                            data['config_info_issue'] = None
                        else:
                            data['config_info_state'] = 'empty'
                            data['config_info_issue'] = "Config info API returned no usable data"
                            logging.info(f"No config info returned for SN: {sn}")

            if data.get('raw') and data.get('machine_info') and data.get('config_info'):
                succeed += 1
                logging.info(f"SN warranty, machine info, and config fetched: {sn}")
        logging.info(f"Fetched {total} SN in total, {succeed} succeed")

    def process_warranty(self):
        """
        Process using raw data already have.
        
        TODO: there are a lot of different kinds of warranty, find the most accurate way
        """
        for sn, data in self.collection.items():
            # get warranty start and end date
            start_date = None
            end_date = None

            if data.get('raw'):
                try:
                    # some SN has empty onsite_date
                    detail_data = data['raw']['detail_data']
                    if detail_data['onsite_data']:
                        warranty_data = detail_data['onsite_data']
                    else:
                        warranty_data = detail_data['warranty_data']

                    for w in warranty_data:
                        _start = w.get('start_date')
                        _end = w.get('end_date')
                        if _start:
                            normalized_start = normalize_warranty_date(_start)
                            if normalized_start:
                                start_date = (
                                    compare_date(start_date, normalized_start, 'start')
                                    if start_date else normalized_start
                                )
                            else:
                                self.collection[sn]['warranty_issue'] = append_issue(
                                    self.collection[sn].get('warranty_issue'),
                                    f"Invalid warranty start date: {_start}",
                                )
                        if _end:
                            normalized_end = normalize_warranty_date(_end)
                            if normalized_end:
                                end_date = (
                                    compare_date(end_date, normalized_end, 'end')
                                    if end_date else normalized_end
                                )
                            else:
                                self.collection[sn]['warranty_issue'] = append_issue(
                                    self.collection[sn].get('warranty_issue'),
                                    f"Invalid warranty end date: {_end}",
                                )
                except Exception as e:
                    self.collection[sn]['warranty_issue'] = append_issue(
                        self.collection[sn].get('warranty_issue'),
                        f"Warranty processing error: {e}",
                    )
                    logging.info(f"Error during process start and end date for {sn}: {e}")
            else:
                logging.info(f"No warranty data available to process for SN: {sn}")

            self.collection[sn]['start_date'] = start_date
            self.collection[sn]['end_date'] = end_date
            self.collection[sn]['model'] = data.get('machine_info', {}).get('product_model')
            self.collection[sn]['mtm'] = data.get('machine_info', {}).get('machine_mtm')
            self.collection[sn]['cpu'] = get_config_value(data.get('config_info'), 'CPU型号')
            self.collection[sn]['ram_factory'] = get_config_value(data.get('config_info'), '内存容量')
            self.collection[sn]['disk_factory'] = get_config_value(data.get('config_info'), '硬盘容量')
            self.collection[sn]['query_status'] = get_query_status(self.collection[sn])
            self.collection[sn]['notes'] = build_notes(self.collection[sn])
            logging.info(
                f"Processed: {sn}, {self.collection[sn]['model']}, {start_date}, {end_date}, "
                f"{self.collection[sn]['mtm']}, {self.collection[sn]['cpu']}, "
                f"{self.collection[sn]['ram_factory']}, {self.collection[sn]['disk_factory']}, "
                f"{self.collection[sn]['query_status']}, {self.collection[sn]['notes']}"
            )

    def add(self, sn_file = None):
        """
        Add SN from file to collection, fetch and update warranty data.
        If no SN file provided, fetch and update existing
        """
        if sn_file:
            self.update_collection(sn_file)
        self.fetch_warranty()
        self.process_warranty()
        self.status()

    def status(self):
        counts = {
            QUERY_STATUS_OK: 0,
            QUERY_STATUS_PARTIAL: 0,
            QUERY_STATUS_NOT_FOUND: 0,
            QUERY_STATUS_INVALID_INPUT: 0,
            QUERY_STATUS_API_ERROR: 0,
        }
        for _, data in self.collection.items():
            counts[get_query_status(data)] += 1
        logging.info(
            "SN query status: "
            f"OK={counts[QUERY_STATUS_OK]}, "
            f"PARTIAL={counts[QUERY_STATUS_PARTIAL]}, "
            f"NOT_FOUND={counts[QUERY_STATUS_NOT_FOUND]}, "
            f"INVALID_INPUT={counts[QUERY_STATUS_INVALID_INPUT]}, "
            f"API_ERROR={counts[QUERY_STATUS_API_ERROR]}"
        )

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

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch Lenovo warranty information and export it to CSV.")
    
    # Adding arguments
    parser.add_argument('-sn', type=str, required=True, help='Path to serial number file (one SN per line)')
    parser.add_argument('-csv', type=str, required=True, help='Path to the CSV file')
    args = parser.parse_args()
    
    # running
    warranty = LenovoWarranty()
    warranty.add(args.sn)
    warranty.save(args.csv)
    
