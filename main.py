import argparse
import csv
import logging
import requests
from datetime import datetime

# web url: https://newthink.lenovo.com.cn/deviceGuarantee.html?selname=AAAAAAAAA
API_BASE = "https://newthink.lenovo.com.cn/api/ThinkHome/Machine/WarrantyListInfo?sn="

def get_warranty(sn):
    """Note: Lenovo API might not available from time to time"""
    url = API_BASE + sn
    response = requests.get(url)
    response.raise_for_status()
    return response.json()
    
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
                    self.collection[sn] = {}

    def fetch_warranty(self):
        """Fetch data and update collection"""
        succeed = 0
        total = 0
        for sn, data in self.collection.items():
            # skip if raw data for the sn exists
            if data.get('raw'):
                continue

            total += 1
    
            # get warranty data and add raw
            try:
                response = get_warranty(sn)
                if response['statusCode'] != 200:
                    logging.info(f"Get warranty return status code: {response['statusCode']}")
                    continue
                raw = response['data']
                if raw:
                    self.collection[sn]['raw'] = raw
                else:
                    continue
            except Exception as e:
                logging.info(f"Error while retrieve and extract warranty data: {e}")
                continue

            succeed += 1
            logging.info(f"SN warranty fetched: {sn}")
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

            try:
                # some SN has empty onsite_date
                _data = data['raw']['detail_data']
                if _data['onsite_data']:
                    data = _data['onsite_data']
                else:
                    data = _data['warranty_data']
                    
                for w in data:
                    _start = w.get('start_date')
                    _end = w.get('end_date')
                    if _start:
                        start_date = compare_date(start_date, _start, 'start') if start_date else _start
                    if _end:
                        end_date = compare_date(end_date, _end, 'end') if end_date else _end
            except Exception as e:
                logging.info(f"Error during process start and end date: {e}")
                
            self.collection[sn]['start_date'] = start_date
            self.collection[sn]['end_date'] = end_date
            logging.info(f"Processed: {sn}, {start_date}, {end_date}")

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
            if data.get('start_date') and data.get('end_date'):
                finished += 1
            else:
                not_finished += 1
        logging.info(f"SN warranty status: {finished} finished, {not_finished} haven't finish")

    def save(self, file = "lenovo_warranty.csv"):
        """Save warranty data to CSV file"""
        data = [['Serial Number', 'Start Date', 'End Date']]
        for sn, warranty in self.collection.items():
            start = warranty.get('start_date')
            end = warranty.get('end_date')
            if start and end:
                data.append([sn, start, end])

        # write to csv
        with open(file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerows(data)

        logging.info(f"Saved warranty data to CSV file '{file}'")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process some integers.")
    
    # Adding arguments
    parser.add_argument('-sn', type=str, required=True, help='Path to serial number file (one SN per line)')
    parser.add_argument('-csv', type=str, required=True, help='Path to the CSV file')
    args = parser.parse_args()
    
    # running
    warranty = LenovoWarranty()
    warranty.add(args.sn)
    warranty.save(args.csv)
    