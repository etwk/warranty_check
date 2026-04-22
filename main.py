import argparse
import csv
import json
import logging
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import urlopen

# web url: https://newthink.lenovo.com.cn/deviceGuarantee.html?selname=AAAAAAAAA
WARRANTY_API_BASE = "https://newthink.lenovo.com.cn/api/ThinkHome/Machine/WarrantyListInfo"
MACHINE_API_BASE = "https://newthink.lenovo.com.cn/api/ThinkHome/Machine/MachineListInfo"
CONFIG_API_BASE = "https://newthink.lenovo.com.cn/api/ThinkHome/Machine/ConfigListInfo"
CSV_HEADERS = [
    'Serial Number',
    'Model',
    'Warranty Start',
    'Warranty End',
    'MTM',
    'CPU',
    'RAM (Factory)',
    'Disk (Factory)',
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


def fetch_json(url, params=None, timeout=20):
    """Fetch JSON from Lenovo APIs without requiring external dependencies."""
    if params:
        url = f"{url}?{urlencode(params)}"

    with urlopen(url, timeout=timeout) as response:
        payload = response.read().decode("utf-8")

    return json.loads(payload)


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


def is_complete_record(warranty):
    """Return True when all CSV output fields have values."""
    return all(warranty.get(field) for field in REQUIRED_OUTPUT_FIELDS)


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
                sn = line.strip().replace('-', '')
                if sn and sn not in self.collection:
                    self.collection[sn] = {
                        'raw_fetched': False,
                        'machine_info_fetched': False,
                        'config_info_fetched': False,
                    }

    def fetch_warranty(self):
        """Fetch warranty and device metadata, then update collection."""
        succeed = 0
        total = 0
        for sn, data in self.collection.items():
            data.setdefault('raw_fetched', 'raw' in data)
            data.setdefault('machine_info_fetched', 'machine_info' in data)
            data.setdefault('config_info_fetched', 'config_info' in data)

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
                    logging.info(f"Error while retrieving warranty data for {sn}: {e}")
                    continue

                data['raw_fetched'] = True
                if warranty_response['statusCode'] != 200:
                    message = warranty_response.get('message', {}).get('info')
                    logging.info(
                        f"Warranty API returned status {warranty_response['statusCode']} for {sn}: {message}"
                    )
                    data['raw'] = None
                else:
                    raw = warranty_response.get('data')
                    data['raw'] = raw
                    if not raw:
                        logging.info(f"No warranty data returned for SN: {sn}")

            if not data['machine_info_fetched']:
                try:
                    machine_response = get_machine_info(sn)
                except Exception as e:
                    logging.info(f"Error while retrieving machine info for {sn}: {e}")
                    continue

                data['machine_info_fetched'] = True
                if machine_response['statusCode'] != 200:
                    message = machine_response.get('message', {}).get('info')
                    logging.info(
                        f"Machine info API returned status {machine_response['statusCode']} for {sn}: {message}"
                    )
                    data['machine_info'] = {}
                else:
                    machine_info = machine_response.get('data', {}).get('data')
                    data['machine_info'] = machine_info or {}
                    if not machine_info:
                        logging.info(f"No machine info returned for SN: {sn}")

            if not data['config_info_fetched']:
                try:
                    config_response = get_config_info(sn)
                except Exception as e:
                    logging.info(f"Error while retrieving config info for {sn}: {e}")
                    continue

                data['config_info_fetched'] = True
                if config_response['statusCode'] != 200:
                    message = config_response.get('message', {}).get('info')
                    logging.info(
                        f"Config info API returned status {config_response['statusCode']} for {sn}: {message}"
                    )
                    data['config_info'] = []
                else:
                    config_info = config_response.get('data') or []
                    data['config_info'] = config_info
                    if not config_info:
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
                            start_date = compare_date(start_date, _start, 'start') if start_date else _start
                        if _end:
                            end_date = compare_date(end_date, _end, 'end') if end_date else _end
                except Exception as e:
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
            logging.info(
                f"Processed: {sn}, {self.collection[sn]['model']}, {start_date}, {end_date}, "
                f"{self.collection[sn]['mtm']}, {self.collection[sn]['cpu']}, "
                f"{self.collection[sn]['ram_factory']}, {self.collection[sn]['disk_factory']}"
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
        finished = 0
        not_finished = 0
        for _, data in self.collection.items():
            if is_complete_record(data):
                finished += 1
            else:
                not_finished += 1
        logging.info(f"SN warranty status: {finished} finished, {not_finished} haven't finish")

    def save(self, file = "lenovo_warranty.csv"):
        """Save warranty data to CSV file"""
        data = [CSV_HEADERS]
        for sn, warranty in self.collection.items():
            if not is_complete_record(warranty):
                logging.info(f"Skipping incomplete record for CSV output: {sn}")
                continue

            data.append([
                sn,
                warranty.get('model'),
                warranty.get('start_date'),
                warranty.get('end_date'),
                warranty.get('mtm'),
                warranty.get('cpu'),
                warranty.get('ram_factory'),
                warranty.get('disk_factory'),
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
    
