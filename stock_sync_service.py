"""Push Salesforce Stock qty + Product price → existing Tally stock masters.

Direction: Salesforce → Tally only. Does not write to Salesforce.
"""

from config import (
    PRODUCT_BASE_PRICE_FIELD,
    PRODUCT_CODE_FIELD,
    STOCK_OBJECT,
    STOCK_PRODUCT_LOOKUP_FIELD,
    STOCK_QUANTITY_FIELD,
    STOCK_WAREHOUSE_FIELD,
    TALLY_BATCH_NAME,
    TALLY_WAREHOUSE_GODOWN_MAP,
    WAREHOUSE_OBJECT,
)
from salesforce_service import _run_query
from tally_service import (
    find_tally_stock_by_part_no,
    get_stock_sync_snapshot,
    update_tally_stock_from_salesforce,
)


def _warehouse_number_from_name(name):

    import re

    match = re.match(r"^warehouse\s+(\d+)\b", str(name or "").strip(), re.IGNORECASE)
    return match.group(1) if match else None


def get_mapped_warehouses():

    records = _run_query(f"SELECT Id, Name FROM {WAREHOUSE_OBJECT} ORDER BY Name")
    by_number = {}
    for row in records:
        number = _warehouse_number_from_name(row.get("Name"))
        if number in TALLY_WAREHOUSE_GODOWN_MAP and number not in by_number:
            by_number[number] = {
                "id": row["Id"],
                "name": row.get("Name"),
                "godown": TALLY_WAREHOUSE_GODOWN_MAP[number],
            }
    return by_number


def query_stock_records(product_codes=None):

    fields = (
        f"Id, Name, {PRODUCT_CODE_FIELD}, {STOCK_WAREHOUSE_FIELD}, "
        f"Warehouse__r.Name, {STOCK_QUANTITY_FIELD}, {STOCK_PRODUCT_LOOKUP_FIELD}, "
        f"Product__r.{PRODUCT_BASE_PRICE_FIELD}, Product__r.Name"
    )
    query = f"SELECT {fields} FROM {STOCK_OBJECT}"

    codes = [str(c).strip() for c in (product_codes or []) if str(c or "").strip()]
    if codes:
        escaped = []
        for code in codes:
            escaped.append("'" + code.replace("\\", "\\\\").replace("'", "\\'") + "'")
        query += f" WHERE {PRODUCT_CODE_FIELD} IN ({', '.join(escaped)})"

    return _run_query(query)


def sync_salesforce_stock_to_tally(product_codes=None):

    """Update Tally stock Opening qty (by godown) + rate from Salesforce.

    - Product_Code__c ↔ Tally Part No (must already exist in Tally)
    - Warehouse N ↔ godown (config map)
    - Stock Quantity__c → that godown Opening balance
    - Product Base_Price__c → Tally OpeningRate
    - Never creates stock items / godowns
    - Never writes back to Salesforce
    """

    warehouses = get_mapped_warehouses()
    if not warehouses:
        return {
            "success": False,
            "message": "No mapped Warehouse 1/2/3 records found in Salesforce.",
            "updated": [],
            "skipped": [],
            "errors": [],
        }

    stock_rows = query_stock_records(product_codes=product_codes)
    if not stock_rows:
        return {
            "success": True,
            "message": "No Salesforce Stock__c rows matched.",
            "updated": [],
            "skipped": [],
            "errors": [],
        }

    # Group SF lines by product code → godown qty + shared price
    by_code = {}
    skipped = []
    for row in stock_rows:
        code = str(row.get(PRODUCT_CODE_FIELD) or "").strip()
        stock_id = row.get("Id")
        warehouse_name = ((row.get("Warehouse__r") or {}).get("Name") or "")
        warehouse_no = _warehouse_number_from_name(warehouse_name)
        godown = TALLY_WAREHOUSE_GODOWN_MAP.get(warehouse_no or "")
        qty = float(row.get(STOCK_QUANTITY_FIELD) or 0)
        product = row.get("Product__r") or {}
        rate = float(product.get(PRODUCT_BASE_PRICE_FIELD) or 0)

        if not code:
            skipped.append({"stock_id": stock_id, "reason": "blank Product_Code__c"})
            continue
        if not godown:
            skipped.append({
                "stock_id": stock_id,
                "code": code,
                "warehouse": warehouse_name,
                "reason": "warehouse not mapped (need Warehouse 1/2/3)",
            })
            continue

        entry = by_code.setdefault(code, {
            "godown_qty": {},
            "rate": 0.0,
            "sf_stocks": [],
        })
        entry["godown_qty"][godown] = qty
        if rate > 0:
            entry["rate"] = rate
        entry["sf_stocks"].append({
            "stock_id": stock_id,
            "stock_name": row.get("Name"),
            "warehouse": warehouse_name,
            "godown": godown,
            "quantity": qty,
        })

    updated = []
    errors = []

    for code, payload in sorted(by_code.items()):
        snapshot = None
        try:
            snapshot = get_stock_sync_snapshot(code)
        except Exception as exc:
            errors.append({"code": code, "reason": f"Tally read failed: {exc}"})
            continue

        if not snapshot or not snapshot.get("name"):
            # Confirm via part-no find in case snapshot parse failed
            found = find_tally_stock_by_part_no(code)
            if not found:
                skipped.append({
                    "code": code,
                    "reason": "Part No not found in Tally (will not create)",
                    "sf_stocks": payload["sf_stocks"],
                })
                continue
            tally_name = found["name"]
            keep_godowns = {}
        else:
            tally_name = snapshot["name"]
            keep_godowns = dict(snapshot.get("godowns") or {})

        # Start from existing Tally godown qtys, overwrite mapped ones from SF
        godown_qty = dict(keep_godowns)
        for godown, qty in payload["godown_qty"].items():
            godown_qty[godown] = qty

        rate = float(payload.get("rate") or 0)
        if rate <= 0 and snapshot:
            rate = float(snapshot.get("rate") or 0)

        try:
            response_text = update_tally_stock_from_salesforce(
                stock_name=tally_name,
                part_no=code,
                godown_qty=godown_qty,
                rate=rate,
                batch_name=TALLY_BATCH_NAME,
            )
        except Exception as exc:
            errors.append({
                "code": code,
                "tally_name": tally_name,
                "reason": str(exc),
                "godown_qty": godown_qty,
                "rate": rate,
            })
            continue

        ok = (
            "<CREATED>1</CREATED>" in (response_text or "")
            or "<ALTERED>1</ALTERED>" in (response_text or "")
            or (
                "<ERRORS>0</ERRORS>" in (response_text or "")
                and "<EXCEPTIONS>0</EXCEPTIONS>" in (response_text or "")
            )
        )
        detail = {
            "code": code,
            "tally_name": tally_name,
            "godown_qty": godown_qty,
            "rate": rate,
            "sf_stocks": payload["sf_stocks"],
            "updated": ok,
            "tally_response": (response_text or "")[:500],
        }
        if ok:
            updated.append(detail)
        else:
            errors.append({**detail, "reason": "Tally Alter did not confirm success"})

    success = not errors
    message = (
        f"Pushed {len(updated)} stock item(s) Salesforce → Tally; "
        f"skipped {len(skipped)}; errors {len(errors)}."
    )
    return {
        "success": success,
        "message": message,
        "direction": "salesforce_to_tally",
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "warehouses": {
            number: {"name": info["name"], "godown": info["godown"]}
            for number, info in warehouses.items()
        },
    }


# Backward-compatible name used by main.py
def sync_tally_stock_to_salesforce(product_codes=None):

    return sync_salesforce_stock_to_tally(product_codes=product_codes)
