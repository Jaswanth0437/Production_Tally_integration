from typing import Optional

from fastapi import Body, FastAPI, Header, Query
from pydantic import BaseModel, Field

from config import (
    DISPATCH_SENT_TO_TALLY_FIELD,
    DISPATCH_TALLY_VOUCHER_NUMBER_FIELD,
    SALESFORCE_AUTH_MODE,
    TRACK_DISPATCH_IN_SALESFORCE,
)
from salesforce_service import (
    get_access_token,
    get_dispatch,
    get_dispatch_bill_to,
    get_dispatch_discount_percent,
    get_dispatch_invoice_meta,
    get_dispatch_line_items,
    get_dispatch_party_name,
    get_dispatch_voucher_type,
    get_opportunity,
    get_opportunity_account_name,
    get_opportunity_amount,
    get_opportunity_close_date,
    get_opportunity_products,
    update_dispatch,
    update_opportunity,
)
from stock_sync_service import sync_tally_stock_to_salesforce
from outstanding_service import get_account_outstanding
from product_sync_service import sync_products_to_tally
from tally_diagnostics import parse_tally_response, read_tally_imp_tail
from tally_service import (
    balance_line_item_amounts,
    check_tally_connection,
    consolidate_line_items,
    create_ledger,
    create_sales_voucher,
    extract_tally_error,
    get_tally_products,
    get_tally_stock_item_name,
    get_voucher_after_create,
    match_line_items_to_tally_stock,
    multi_rate_product_codes,
    parse_voucher_type_option,
    resolve_dispatch_godown,
    to_tally_voucher_date,
)

app = FastAPI()


class DispatchInvoiceRequest(BaseModel):
    created_by: Optional[str] = Field(
        default=None,
        description="Salesforce user name to show in Tally voucher narration",
    )


class StockSyncRequest(BaseModel):
    product_codes: Optional[list[str]] = Field(
        default=None,
        description=(
            "Optional Salesforce Product_Code__c list to push into Tally. "
            "Example: [\"3MMSER114\"]. Omit or null to sync all Stock__c rows."
        ),
        examples=[["3MMSER114", "1MMSER107"]],
    )


class ProductSyncRequest(BaseModel):
    product_id: str = Field(
        ...,
        description="Salesforce Product__c Id to create in Tally (single record only).",
        examples=["a0Xfw000000XXXX"],
        min_length=1,
    )


def _resolve_created_by(*values):
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _normalize_opportunity_line(product):

    if "name" in product:
        return product

    product_info = product["PricebookEntry"]["Product2"]

    return {
        "name": product_info["Name"],
        "code": product_info.get("ProductCode", ""),
        "quantity": product["Quantity"],
        "unit_price": product["UnitPrice"],
        "total_price": product["TotalPrice"],
    }


def _print_line_items(line_items):

    print("\nLine Items")
    print("=" * 50)

    for item in line_items:
        print(f"Name  : {item['name']}")
        print(f"Code  : {item.get('code', '')}")
        if item.get("tally_stock_name"):
            print(f"Tally : {item['tally_stock_name']}")
        print(f"Qty   : {item['quantity']}")
        print(f"Rate  : {item['unit_price']}")
        print(f"Total : {item['total_price']}")
        print("-" * 50)


def _create_tally_billing(
    account_name,
    bill_to,
    amount,
    reference_name,
    reference_id,
    voucher_date,
    line_items,
    source_label,
    invoice_meta=None,
    update_callback=None,
    voucher_type=None,
    created_by=None,
    discount_percent=0,
):

    print("\n" + "=" * 70)
    print(f"Reference : {reference_name}")
    print(f"Reference Id : {reference_id}")
    print(f"Customer  : {account_name}")
    print(f"Amount    : {amount}")
    print(f"Voucher Date : {voucher_date}")
    print(f"Voucher Type : {voucher_type}")
    if created_by:
        print(f"Created By  : {created_by}")
    if discount_percent:
        print(f"Discount  : {discount_percent}%")
    print("=" * 70)

    connection = check_tally_connection()
    print("\nTally Company Check")
    print(
        f"Company: {connection.get('company_name')}, "
        f"State: {connection.get('company_state')}, "
        f"Expected State: {connection.get('expected_state')}"
    )
    if not connection.get("ok"):
        print(f"Blocked: {connection.get('message')}")
        return {
            "success": False,
            "message": connection.get("message")
            or "Tally company/state check failed. Voucher not created.",
        }

    ledger_response = create_ledger(account_name, bill_to=bill_to)
    print("\nLedger Response")
    print(ledger_response)

    print("\nMatching Salesforce codes to Tally stock Part No")
    stock_match = match_line_items_to_tally_stock(line_items)
    print(stock_match.get("message"))
    for row in stock_match.get("matched") or []:
        print(
            f"  {row.get('code')} -> {row.get('tally_name')} "
            f"(qty={row.get('quantity')}, rate={row.get('unit_price')})"
        )

    if not stock_match.get("success"):
        return {
            "success": False,
            "message": stock_match.get("message"),
            "missing_codes": stock_match.get("missing_codes") or [],
        }

    line_items = stock_match["line_items"]
    _print_line_items(line_items)

    if not str(voucher_type or "").strip():
        return {
            "success": False,
            "message": (
                "Invoice voucher type is required. Set Invoice_Voucher_Type__c on Dispatch."
            ),
        }

    invoice_meta = invoice_meta or {}
    godown_map = resolve_dispatch_godown(invoice_meta)
    print("\nWarehouse → Tally Godown")
    print(godown_map.get("message"))
    if not godown_map.get("success"):
        return {
            "success": False,
            "message": godown_map.get("message"),
            "warehouse_name": godown_map.get("warehouse_name") or "",
        }
    invoice_meta = {
        **invoice_meta,
        "tally_godown_name": godown_map["godown_name"],
    }

    voucher_response = create_sales_voucher(
        account_name,
        amount,
        reference_name,
        reference_id,
        voucher_date,
        line_items=line_items,
        source_label=source_label,
        bill_reference=reference_name,
        invoice_meta=invoice_meta,
        bill_to=bill_to,
        voucher_type=voucher_type,
        created_by=created_by,
        discount_percent=discount_percent,
    )

    print("\nVoucher Response")
    print(voucher_response)

    if "<CREATED>1</CREATED>" in voucher_response:
        salesforce_updated = False
        # Use the real Tally voucher type name (last segment of Group/Ledger/Type).
        tally_lookup_type = (
            parse_voucher_type_option(voucher_type).get("voucher_type")
            or voucher_type
        )
        voucher_info = get_voucher_after_create(
            voucher_response,
            voucher_date,
            reference_id,
            voucher_type=tally_lookup_type,
            reference_name=reference_name,
        )
        voucher_number = (voucher_info or {}).get("number")
        tally_voucher_type = (voucher_info or {}).get("type") or voucher_type

        if voucher_number:
            voucher_source = (voucher_info or {}).get("source") or "tally"
            print(
                f"Tally Voucher ({voucher_source}) : Type={tally_voucher_type}, "
                f"Number=#{voucher_number}"
            )
        else:
            print(
                "Tally voucher created but Day Book number not found yet "
                f"(date={voucher_date}, ref={reference_id})"
            )

        if update_callback:
            update_callback(
                voucher_number=voucher_number,
                voucher_type=tally_voucher_type,
            )
            salesforce_updated = True
            print("Salesforce Updated : Sent_To_Tally__c = True")

        vch_type_label = str(tally_voucher_type or "").strip() or "Unknown"

        if voucher_number:
            success_message = (
                f"Voucher Created Successfully. "
                f"Type: {vch_type_label}, Tally Voucher #{voucher_number}"
            )
        else:
            success_message = (
                f"Voucher Created Successfully. Type: {vch_type_label}. "
                "Tally voucher number was not found from Tally yet."
            )

        return {
            "success": True,
            "message": success_message,
            "voucher_type": tally_voucher_type,
            "voucher_number": voucher_number,
            "salesforce_updated": salesforce_updated,
        }

    tally_error = extract_tally_error(voucher_response)
    tally_parsed = parse_tally_response(voucher_response)
    tally_log = read_tally_imp_tail(lines=40)

    return {
        "success": False,
        "message": tally_error or (
            "Voucher import failed in Tally (see tally_import_log and how_to_check_tally_errors)."
        ),
        "tally_response": voucher_response,
        "tally_summary": tally_parsed["summary"],
        "tally_import_log": tally_log,
        "how_to_check_tally_errors": [
            "In TallyPrime, press Alt+G (Gateway of Tally), type 'Import Exceptions' and open it.",
            "Or press F1 > Troubleshooting > Event Log.",
            "Or open C:\\Program Files\\TallyPrime\\tally.imp in Notepad (last lines show import results).",
            "TallyPrime 3.0+ often hides the error text in the API response; the UI screens above show the real reason.",
        ],
    }


# @app.get("/")
# def home():

#     return {
#         "status": "running"
#     }

@app.api_route("/", methods=["GET", "HEAD"])
async def home():
    return {"status": "running"}


@app.get("/test-salesforce-auth")
def test_salesforce_auth():

    try:
        token = get_access_token()

        return {
            "success": True,
            "auth_mode": SALESFORCE_AUTH_MODE,
            "instance_url": token.get("instance_url"),
        }
    except Exception as e:
        return {
            "success": False,
            "message": str(e)
        }


@app.get("/tally-connection-test")
def tally_connection_test():

    return check_tally_connection()


@app.get("/get-tally-products")
def get_tally_products_endpoint():

    try:
        products = get_tally_products()
        return {
            "success": True,
            "count": len(products),
            "products": products,
        }
    except Exception as e:
        return {
            "success": False,
            "count": 0,
            "products": [],
            "message": str(e),
        }


@app.get("/account-outstanding/{account_id}")
def account_outstanding_endpoint(account_id: str):

    """Read-only: Salesforce Account → Tally outstanding amount + days.

    Success body: { success, outstanding_amount, outstanding_days }
    """

    connection = check_tally_connection()
    if not connection.get("ok"):
        return {
            "success": False,
            "message": connection.get("message")
            or "Tally company/state check failed.",
        }

    try:
        return get_account_outstanding(account_id)
    except Exception as e:
        return {
            "success": False,
            "message": str(e),
        }


@app.post("/sync-products-to-tally")
def sync_products_to_tally_endpoint(payload: ProductSyncRequest):

    """Create one missing Tally stock item from a Salesforce Product__c Id.

    Body: { "product_id": "a0X..." }

    Mapping:
      Product_Info__c → "{Product Family} - {Product Info}"
      Product_Code__c → Part No
      Product_Family__c → root stock group
      HSN_Code__c → HSN/SAC

    Lamination Sheet only:
      Lamination Sheet (root) → thickness group (1MM/2.5MM/3MM) → stock item

    If Product Code already exists in Tally → error, not created.
    """

    connection = check_tally_connection()
    if not connection.get("ok"):
        return {
            "success": False,
            "message": connection.get("message")
            or "Tally company/state check failed. Product sync blocked.",
            "created": [],
            "skipped": [],
            "errors": [],
        }

    try:
        return sync_products_to_tally(payload.product_id)
    except Exception as e:
        return {
            "success": False,
            "message": str(e),
            "created": [],
            "skipped": [],
            "errors": [{"reason": str(e)}],
        }


@app.post("/sync-tally-stock")
def sync_tally_stock_endpoint(payload: Optional[StockSyncRequest] = None):

    """Push Salesforce Stock qty + Product price → existing Tally stock.

    - Salesforce Product_Code__c → Tally Part No (must already exist)
    - Stock Quantity__c + Warehouse 1/2/3 → Tally godown opening qty + rate/value
    - Product Base_Price__c → Tally OpeningRate

    Body examples:
      { "product_codes": ["3MMSER114"] }
      {}
    """

    connection = check_tally_connection()
    if not connection.get("ok"):
        return {
            "success": False,
            "message": connection.get("message")
            or "Tally company/state check failed. Stock sync blocked.",
        }

    request = payload or StockSyncRequest()
    product_codes = request.product_codes

    try:
        result = sync_tally_stock_to_salesforce(product_codes=product_codes)
        return result
    except Exception as e:
        return {
            "success": False,
            "message": str(e),
            "updated": [],
            "skipped": [],
            "errors": [{"reason": str(e)}],
        }


@app.get("/tally-import-log")
def tally_import_log_endpoint(lines: int = 60):

    return read_tally_imp_tail(lines=lines)


@app.post("/create-voucher/{opportunity_id}")
def create_voucher(opportunity_id: str):

    opp = get_opportunity(opportunity_id)

    if not opp:
        return {
            "success": False,
            "message": "Opportunity Not Found"
        }

    if opp.get("Sent_To_Tally__c"):
        return {
            "success": False,
            "message": "Voucher Already Created"
        }

    products = get_opportunity_products(opportunity_id)
    line_items = [_normalize_opportunity_line(product) for product in products]
    _print_line_items(line_items)

    account_name = get_opportunity_account_name(opp)
    amount = get_opportunity_amount(opp)

    if not amount:
        amount = sum(item["total_price"] for item in line_items)

    line_items, amount = balance_line_item_amounts(line_items, amount)
    line_items = consolidate_line_items(line_items)
    voucher_date = to_tally_voucher_date(get_opportunity_close_date(opp))

    try:
        return _create_tally_billing(
            account_name=account_name,
            bill_to=None,
            amount=amount,
            reference_name=opp["Name"],
            reference_id=opp["Id"],
            voucher_date=voucher_date,
            line_items=line_items,
            source_label="Salesforce Opportunity",
            invoice_meta=None,
            update_callback=lambda: update_opportunity(opportunity_id),
        )
    except Exception as e:
        return {
            "success": False,
            "message": str(e)
        }


@app.post("/create-dispatch-invoice/{dispatch_id}")
def create_dispatch_invoice(
    dispatch_id: str,
    created_by: str | None = Query(
        default=None,
        description="Salesforce user name to show in Tally voucher narration",
    ),
    x_created_by: str | None = Header(
        default=None,
        alias="X-Created-By",
    ),
    payload: DispatchInvoiceRequest | None = Body(default=None),
):

    try:
        resolved_created_by = _resolve_created_by(
            created_by,
            x_created_by,
            payload.created_by if payload else None,
        )

        dispatch = get_dispatch(dispatch_id)

        if not dispatch:
            return {
                "success": False,
                "message": (
                    "Dispatch Not Found. Use a Dispatch__c Id (starts with a07), "
                    "not an Opportunity Id (starts with a0C)."
                ),
            }

        if TRACK_DISPATCH_IN_SALESFORCE and dispatch.get(DISPATCH_SENT_TO_TALLY_FIELD):
            existing_voucher = dispatch.get(DISPATCH_TALLY_VOUCHER_NUMBER_FIELD) or ""
            vch_type_label = get_dispatch_voucher_type(dispatch) or "Unknown"
            message = f"Tally Invoice Already Created. Type: {vch_type_label}"
            if existing_voucher:
                message = f"{message}, Tally Voucher #{existing_voucher}"
            return {
                "success": False,
                "message": message,
            }

        line_items = get_dispatch_line_items(dispatch["Id"], dispatch)
        discount_percent = get_dispatch_discount_percent(dispatch)

        if not line_items:
            return {
                "success": False,
                "message": "No Dispatch Line Items Found"
            }

        _print_line_items(line_items)

        account_name = get_dispatch_party_name(dispatch)
        bill_to = get_dispatch_bill_to(dispatch)
        invoice_meta = get_dispatch_invoice_meta(dispatch)

        amount = sum(item["total_price"] for item in line_items)

        if not amount:
            amount = get_opportunity_amount(dispatch.get("Opportunity__r") or {})

        if not amount:
            amount = sum(
                float(item["quantity"] or 0)
                for item in line_items
            )

        if not amount:
            return {
                "success": False,
                "message": "No billing amount found on Dispatch. Add line item prices or Total_Bundles__c.",
            }

        line_items, amount = balance_line_item_amounts(line_items, amount)
        line_items = consolidate_line_items(line_items)

        dispatch_date = (
            dispatch.get("Dispatched_Date__c")
            or dispatch.get("Planned_Date__c")
        )
        voucher_date = to_tally_voucher_date(dispatch_date)
        voucher_type = get_dispatch_voucher_type(dispatch)

        if not voucher_type:
            return {
                "success": False,
                "message": (
                    "Invoice_Voucher_Type__c is required on Dispatch "
                    "before creating a Tally invoice."
                ),
            }

        update_callback = (
            (lambda voucher_number=None, voucher_type=None: update_dispatch(
                dispatch_id, voucher_number, voucher_type
            ))
            if TRACK_DISPATCH_IN_SALESFORCE
            else None
        )

        return _create_tally_billing(
            account_name=account_name,
            bill_to=bill_to,
            amount=amount,
            reference_name=dispatch["Name"],
            reference_id=dispatch["Id"],
            voucher_date=voucher_date,
            line_items=line_items,
            source_label="Salesforce Dispatch",
            invoice_meta=invoice_meta,
            update_callback=update_callback,
            voucher_type=voucher_type,
            created_by=resolved_created_by,
            discount_percent=discount_percent,
        )
    except Exception as e:
        return {
            "success": False,
            "message": str(e)
        }
