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
  - Query Status
  - Notes

Each unique normalized SN is kept in the CSV. Input values are normalized by
trimming spaces, removing hyphens, and uppercasing; duplicate lines that
normalize to the same SN produce a single CSV row. Missing values are exported
as `N/A`, and `Query Status`/`Notes` explain invalid input, partial results,
not-found responses, or API errors.

Transient Lenovo API/network failures are retried automatically before a row is
marked with an API error.

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
