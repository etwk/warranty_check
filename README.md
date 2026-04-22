Fetch warranty info and save to CSV.
Supported vendor:
  - Lenovo: laptop

Current CSV columns:
  - Serial Number
  - Model
  - Warranty Start
  - Warranty End
  - MTM
  - CPU
  - RAM (Factory)
  - Disk (Factory)

Rows are exported only when all CSV columns above have values.

## How-to
### Use the script
```
python3 main.py -sn "./sn.txt" -csv "./warranty.csv"
```

### Use the function
The class `LenovoWarranty` stores raw fetched warranty data and processed data in `self.collection`, it will avoid fetch SN that are already fetched.

- Initiate: `warranty = LenovoWarranty()`
- Add SN then fetch and process: `warranty.add("./sn.txt")` (run this multiple times to add from different files)
- Write finished to CSV: `warranty.save("./warranty.csv")`
- Check current status: `warranty.status()`

## Todo
- [ ] Avoid duplicated processing
- [ ] Find the correct way to process Lenovo warranty data based on how Lenovo website processing using JS
