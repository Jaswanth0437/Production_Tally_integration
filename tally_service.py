import re
import xml.etree.ElementTree as ET

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from xml.sax.saxutils import escape
from config import (
    TALLY_BATCH_NAME,
    TALLY_CGST_LEDGER,
    TALLY_CGST_RATE,
    TALLY_COMPANY_GSTIN,
    TALLY_COMPANY_NAME,
    TALLY_COMPANY_STATE,
    TALLY_DEFAULT_HSN_CODE,
    TALLY_ENABLE_GST,
    TALLY_GST_INVOICE_VIEW,
    TALLY_GODOWN_NAME,
    TALLY_WAREHOUSE_GODOWN_MAP,
    TALLY_IGST_LEDGER,
    TALLY_IGST_RATE,
    TALLY_PASSWORD,
    TALLY_ROUND_OFF_LEDGER,
    TALLY_ROUND_OFF_PARENT,
    TALLY_ROUNDING_LIMIT,
    TALLY_ROUNDING_METHOD,
    TALLY_ENABLE_ROUND_OFF,
    TALLY_SGST_LEDGER,
    TALLY_SGST_RATE,
    TALLY_UNIT_NAME,
    TALLY_URL,
    TALLY_USERNAME,
    TALLY_VOUCHER_MODE,
)

DEFAULT_SALES_LEDGER = "Sales"


def _normalize_tally_url(url):

    return str(url or "").strip().rstrip("/")


def _tally_session():

    session = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.5,
        status_forcelist=(502, 503, 504),
        allowed_methods=("POST",),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


_TALLY_HTTP = _tally_session()

def _tally_headers():

    headers = {"Content-Type": "text/xml"}
    if "ngrok" in str(TALLY_URL).lower():
        headers["ngrok-skip-browser-warning"] = "true"
    return headers


def _tally_auth():

    username = str(TALLY_USERNAME or "").strip()
    if username:
        return (username, TALLY_PASSWORD or "")
    return None


def _tally_static_variables(extra_tags=""):

    company = str(TALLY_COMPANY_NAME or "").strip()
    if not company and not extra_tags:
        return ""

    lines = []
    if company:
        lines.append(f"<SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY>")
    if extra_tags:
        lines.append(extra_tags)

    return "<STATICVARIABLES>\n" + "\n".join(lines) + "\n</STATICVARIABLES>"


def _import_requestdesc(report_name):

    static = _tally_static_variables()
    if static:
        return f"""<REQUESTDESC>
<REPORTNAME>{report_name}</REPORTNAME>
{static}
</REQUESTDESC>"""

    return f"""<REQUESTDESC>
<REPORTNAME>{report_name}</REPORTNAME>
</REQUESTDESC>"""


def _tally_post(xml):

    return _TALLY_HTTP.post(
        _normalize_tally_url(TALLY_URL),
        data=xml,
        headers=_tally_headers(),
        auth=_tally_auth(),
        timeout=60,
    )


def _tally_connection_hint(exc=None):

    url = _normalize_tally_url(TALLY_URL)
    hints = [
        "Confirm TallyPrime is open and F1 → Settings → Connectivity → TallyPrime acts as Server = Yes.",
        "If ngrok is used, restart ngrok and copy the new https URL into TALLY_URL in config.py.",
        "If FastAPI and Tally run on the same PC, use TALLY_URL = \"http://localhost:9000\" instead of ngrok.",
    ]
    if exc and "SSL" in str(exc):
        hints.insert(
            0,
            "SSL error usually means the ngrok tunnel is down, expired, or the URL changed.",
        )
    return {
        "tally_url": url,
        "hints": hints,
    }


def check_tally_connection():

    # Same import path as real invoices — verifies URL, auth, and company name.
    xml = f"""<ENVELOPE>
<HEADER>
<TALLYREQUEST>Import Data</TALLYREQUEST>
</HEADER>
<BODY>
<IMPORTDATA>
{_import_requestdesc("All Masters")}
<REQUESTDATA>
</REQUESTDATA>
</IMPORTDATA>
</BODY>
</ENVELOPE>"""

    try:
        response = _tally_post(xml)
        text = response.text
    except requests.RequestException as exc:
        hint = _tally_connection_hint(exc)
        return {
            "ok": False,
            "connected": False,
            "company_ok": False,
            "company_configured": bool(str(TALLY_COMPANY_NAME or "").strip()),
            "company_name": TALLY_COMPANY_NAME,
            "auth_configured": bool(str(TALLY_USERNAME or "").strip()),
            "message": "Cannot reach Tally. Check TALLY_URL, ngrok, and that Tally server is on.",
            "error": str(exc),
            **hint,
        }

    error = extract_tally_error(text)
    connected = response.status_code == 200 and (
        "<RESPONSE>" in text or "<ENVELOPE>" in text
    )
    company_error = error and "could not find company" in error.lower()

    expected_name = str(TALLY_COMPANY_NAME or "").strip()
    expected_state = str(TALLY_COMPANY_STATE or "").strip()

    company_info = get_tally_company_info() if connected else None
    company_name = (company_info or {}).get("name") or ""
    company_state = (company_info or {}).get("state") or ""

    # Detect multiple open companies sharing the configured name. Over HTTP a
    # request can only target by name, so duplicates cannot be routed by state.
    open_companies = get_open_companies() if connected else []
    same_name_count = sum(
        1 for name in open_companies
        if name.strip().lower() == expected_name.lower()
    )
    duplicate_names = same_name_count > 1

    company_ok = connected and not company_error and bool(company_name)
    state_ok = (
        bool(company_state)
        and bool(expected_state)
        and company_state.strip().lower() == expected_state.lower()
    )

    ok = bool(connected and company_ok and state_ok and not duplicate_names)

    if not connected:
        message = "Unexpected response from Tally."
    elif company_error or not company_name:
        message = (
            f"Tally is reachable but company '{TALLY_COMPANY_NAME}' was not found. "
            "Fix TALLY_COMPANY_NAME in config.py."
        )
    elif duplicate_names:
        message = (
            f"{same_name_count} companies named '{expected_name}' are open in Tally. "
            "Cannot decide which one by state over HTTP. Keep only the "
            f"'{expected_state}' company open, then retry. Voucher not sent."
        )
    elif not state_ok:
        message = (
            f"Company '{company_name}' is open but its state is "
            f"'{company_state or 'unknown'}', expected '{expected_state}'. "
            "Voucher not sent to Tally."
        )
    else:
        message = (
            f"Tally is reachable. Company '{company_name}' "
            f"(state '{company_state}') is valid. Ready for invoices."
        )

    return {
        "ok": ok,
        "connected": connected,
        "company_ok": company_ok,
        "company_configured": bool(expected_name),
        "company_name": company_name or TALLY_COMPANY_NAME,
        "company_state": company_state,
        "expected_state": expected_state,
        "state_ok": state_ok,
        "open_company_count": len(open_companies),
        "same_name_company_count": same_name_count,
        "duplicate_company_names": duplicate_names,
        "auth_configured": bool(str(TALLY_USERNAME or "").strip()),
        "message": message,
        "error": error,
        "raw_response": text[:2000],
        **_tally_connection_hint(),
    }


def get_open_companies():

    """Return the list of currently open/loaded company names in Tally.

    Used to detect the case where more than one company shares the configured
    name (client has same-named companies per state). Over HTTP a request can
    only target a company by name, so duplicates cannot be disambiguated.
    """

    xml = """<ENVELOPE>
<HEADER>
<VERSION>1</VERSION>
<TALLYREQUEST>EXPORT</TALLYREQUEST>
<TYPE>COLLECTION</TYPE>
<ID>List of Companies</ID>
</HEADER>
<BODY>
<DESC>
<STATICVARIABLES>
<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
</STATICVARIABLES>
<TDL>
<TDLMESSAGE>
<COLLECTION NAME="List of Companies" ISMODIFY="No">
<TYPE>Company</TYPE>
<NATIVEMETHOD>NAME</NATIVEMETHOD>
</COLLECTION>
</TDLMESSAGE>
</TDL>
</DESC>
</BODY>
</ENVELOPE>"""

    try:
        text = _tally_post(xml).text
    except requests.RequestException:
        return []

    try:
        root = ET.fromstring(_sanitize_tally_xml(text))
    except ET.ParseError:
        return []

    names = []
    for node in root.findall(".//COMPANY"):
        name = _clean_tally_text(node.get("NAME") or node.findtext("NAME") or "")
        if name:
            names.append(name)
    return names


def get_tally_company_info():

    """Read the open company's name and state from Tally.

    Returns {"name": str, "state": str} or None if the company can't be read.
    """

    xml = f"""<ENVELOPE>
<HEADER>
<VERSION>1</VERSION>
<TALLYREQUEST>EXPORT</TALLYREQUEST>
<TYPE>Object</TYPE>
<SUBTYPE>Company</SUBTYPE>
<ID TYPE="Name">{escape(TALLY_COMPANY_NAME)}</ID>
</HEADER>
<BODY>
<DESC>
<STATICVARIABLES>
<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
</STATICVARIABLES>
<FETCHLIST>
<FETCH>NAME</FETCH>
<FETCH>STATENAME</FETCH>
<FETCH>PRIORSTATENAME</FETCH>
</FETCHLIST>
</DESC>
</BODY>
</ENVELOPE>"""

    try:
        text = _tally_post(xml).text
    except requests.RequestException:
        return None

    try:
        root = ET.fromstring(_sanitize_tally_xml(text))
    except ET.ParseError:
        return None

    # Tally returns two <COMPANY> nodes: a count inside <CMPINFO> and the
    # real master under <DATA><TALLYMESSAGE>. Pick the one with real data.
    company = None
    for node in root.findall(".//COMPANY"):
        if node.get("NAME") or node.findtext("NAME") or node.findtext("STATENAME"):
            company = node
            break

    if company is None:
        return None

    name = _clean_tally_text(
        company.get("NAME")
        or company.findtext("NAME")
        or ""
    )
    state = _clean_tally_text(
        company.findtext("STATENAME")
        or company.findtext("PRIORSTATENAME")
        or ""
    )

    if not name:
        return None

    return {"name": name, "state": state}


def _sanitize_tally_xml(text):
    """Remove invalid control-character entities Tally embeds in exported XML."""
    text = re.sub(
        r"&#(?:0|(?:1[0-9]|2[0-9]|3[0-1]|[1-9]));",
        "",
        text,
    )
    return re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", text)


def _clean_tally_text(value):
    return value.strip() if value else value


def _parse_tally_qty_string(value):

    text = str(value or "").strip()
    if not text:
        return 0.0, ""

    parts = text.split(None, 1)
    try:
        quantity = float(parts[0].replace(",", ""))
    except ValueError:
        return 0.0, text

    unit = parts[1].strip() if len(parts) > 1 else ""
    return quantity, unit


def _parse_tally_rate_string(value):

    text = str(value or "").strip()
    if not text:
        return 0.0, ""

    if "/" in text:
        rate_part, unit = text.split("/", 1)
        try:
            return float(rate_part.replace(",", "").strip()), unit.strip()
        except ValueError:
            return 0.0, unit.strip()

    try:
        return float(text.replace(",", "")), ""
    except ValueError:
        return 0.0, ""


def _stock_item_hsn_code(item):

    for tag in ("GSTHSNCODE", "GSTHSNNAME", "HSNCODE", "HSN"):
        value = _clean_tally_text(item.findtext(tag))
        if value:
            return value
    return ""


def _stock_item_rate_per(item):

    opening_rate, opening_unit = _parse_tally_rate_string(item.findtext("OPENINGRATE"))
    if opening_rate:
        return opening_rate, opening_unit, "OpeningRate"

    standard_price, standard_unit = _parse_tally_rate_string(item.findtext("STANDARDPRICE"))
    if standard_price:
        return standard_price, standard_unit, "StandardPrice"

    return 0.0, _clean_tally_text(item.findtext("BASEUNITS")) or "", ""


def _tally_qty(qty):
    q = float(qty)
    if q == int(q):
        return str(int(q))
    return f"{q}"


def _tally_rate(rate):
    return f"{float(rate):.2f}/{TALLY_UNIT_NAME}"


def _is_intra_state_sale(bill_to):

    buyer_state = str((bill_to or {}).get("state") or "").strip().lower()
    company_state = str(TALLY_COMPANY_STATE or "").strip().lower()
    return bool(buyer_state and company_state and buyer_state == company_state)


def _build_line_accounting_allocations(
    item,
    bill_to=None,
    invoice_view=False,
    sales_ledger=DEFAULT_SALES_LEDGER,
):

    amount_str = _tally_amount(item["total_price"])
    ledger = escape(str(sales_ledger or DEFAULT_SALES_LEDGER))
    return f"""<ACCOUNTINGALLOCATIONS.LIST>
<LEDGERNAME>{ledger}</LEDGERNAME>
<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
<AMOUNT>{amount_str}</AMOUNT>
</ACCOUNTINGALLOCATIONS.LIST>"""


def _build_tax_ledger_entries(taxable_amount, bill_to, invoice_view=False):

    if not TALLY_ENABLE_GST or taxable_amount <= 0:
        return "", taxable_amount

    ledger_tag = "LEDGERENTRIES.LIST" if invoice_view else "ALLLEDGERENTRIES.LIST"

    if _is_intra_state_sale(bill_to):
        cgst = round(taxable_amount * float(TALLY_CGST_RATE) / 100, 2)
        sgst = round(taxable_amount * float(TALLY_SGST_RATE) / 100, 2)
        party_total = round(taxable_amount + cgst + sgst, 2)
        cgst_duty = ""
        sgst_duty = ""
        if invoice_view:
            cgst_duty = f"""<GSTDUTYHEAD>Central Tax</GSTDUTYHEAD>
<RATEDETAILS.LIST>
<GSTRATEDUTYHEAD>Central Tax</GSTRATEDUTYHEAD>
<GSTRATEVALUATIONTYPE>Based on Value</GSTRATEVALUATIONTYPE>
<GSTRATE>{_gst_rate_value(TALLY_CGST_RATE)}</GSTRATE>
<GSTRATEPERUNIT>0</GSTRATEPERUNIT>
</RATEDETAILS.LIST>
"""
            sgst_duty = f"""<GSTDUTYHEAD>State Tax</GSTDUTYHEAD>
<RATEDETAILS.LIST>
<GSTRATEDUTYHEAD>State Tax</GSTRATEDUTYHEAD>
<GSTRATEVALUATIONTYPE>Based on Value</GSTRATEVALUATIONTYPE>
<GSTRATE>{_gst_rate_value(TALLY_SGST_RATE)}</GSTRATE>
<GSTRATEPERUNIT>0</GSTRATEPERUNIT>
</RATEDETAILS.LIST>
"""
        tax_entries = f"""<{ledger_tag}>
<LEDGERNAME>{escape(TALLY_CGST_LEDGER)}</LEDGERNAME>
{cgst_duty}<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
<AMOUNT>{_tally_amount(cgst)}</AMOUNT>
</{ledger_tag}>
<{ledger_tag}>
<LEDGERNAME>{escape(TALLY_SGST_LEDGER)}</LEDGERNAME>
{sgst_duty}<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
<AMOUNT>{_tally_amount(sgst)}</AMOUNT>
</{ledger_tag}>
"""
        return tax_entries, party_total

    igst = round(taxable_amount * float(TALLY_IGST_RATE) / 100, 2)
    party_total = round(taxable_amount + igst, 2)
    duty_tag = ""
    if invoice_view:
        duty_tag = f"""<GSTDUTYHEAD>Integrated Tax</GSTDUTYHEAD>
<RATEDETAILS.LIST>
<GSTRATEDUTYHEAD>Integrated Tax</GSTRATEDUTYHEAD>
<GSTRATEVALUATIONTYPE>Based on Value</GSTRATEVALUATIONTYPE>
<GSTRATE>{_gst_rate_value(TALLY_IGST_RATE)}</GSTRATE>
<GSTRATEPERUNIT>0</GSTRATEPERUNIT>
</RATEDETAILS.LIST>
"""
    tax_entries = f"""<{ledger_tag}>
<LEDGERNAME>{escape(TALLY_IGST_LEDGER)}</LEDGERNAME>
{duty_tag}<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
<AMOUNT>{_tally_amount(igst)}</AMOUNT>
</{ledger_tag}>
"""
    return tax_entries, party_total


def _round_to_nearest_rupee(amount):

    from decimal import Decimal, ROUND_HALF_UP

    amount_dec = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    rounded = amount_dec.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    round_off = float(rounded - amount_dec)

    if abs(round_off) < 0.005:
        return float(amount_dec), 0.0

    return float(rounded), round(round_off, 2)


def _build_round_off_ledger_entry(round_off_amount, invoice_view=False):

    if not round_off_amount:
        return ""

    ledger_tag = "LEDGERENTRIES.LIST" if invoice_view else "ALLLEDGERENTRIES.LIST"
    amount_str = _tally_amount(round_off_amount)
    round_type = escape(TALLY_ROUNDING_METHOD)

    return f"""<{ledger_tag}>
<ROUNDTYPE>{round_type}</ROUNDTYPE>
<LEDGERNAME>{escape(TALLY_ROUND_OFF_LEDGER)}</LEDGERNAME>
<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
<AMOUNT>{amount_str}</AMOUNT>
</{ledger_tag}>
"""


def _tally_amount(value):
    return f"{float(value):.2f}"


def to_tally_voucher_date(date_value=None):
    """Convert YYYY-MM-DD to YYYYMMDD. Tally EDU allows 1st, 2nd, last day only."""
    from datetime import datetime
    import calendar

    if not date_value:
        dt = datetime.today()
    elif "-" in str(date_value):
        dt = datetime.strptime(str(date_value), "%Y-%m-%d")
    else:
        return str(date_value)

    last_day = calendar.monthrange(dt.year, dt.month)[1]

    if dt.day in (1, 2, last_day):
        return dt.strftime("%Y%m%d")

    return dt.replace(day=2).strftime("%Y%m%d")


def _normalize_line_item(line_item):

    if "PricebookEntry" in line_item:
        product = line_item["PricebookEntry"]["Product2"]
        normalized = {
            "name": product["Name"],
            "product_name": product["Name"],
            "code": product.get("ProductCode", ""),
            "description": product.get("Description", "") or "",
            "hsn": line_item.get("hsn") or TALLY_DEFAULT_HSN_CODE,
            "quantity": line_item["Quantity"],
            "unit_price": line_item["UnitPrice"],
            "total_price": line_item["TotalPrice"],
        }
    else:
        normalized = {
            "name": line_item["name"],
            "product_name": line_item.get("product_name") or line_item["name"],
            "product_info": line_item.get("product_info", ""),
            "code": line_item.get("code", ""),
            "description": line_item.get("description", "") or "",
            "hsn": line_item.get("hsn") or TALLY_DEFAULT_HSN_CODE,
            "quantity": line_item["quantity"],
            "unit_price": line_item["unit_price"],
            "total_price": line_item["total_price"],
        }

    # Keep Part No → Tally Name resolution through voucher XML build
    for key in ("tally_stock_name", "tally_part_no"):
        value = str(line_item.get(key) or "").strip()
        if value:
            normalized[key] = value

    return normalized


def _item_hsn(item):

    return str(item.get("hsn") or TALLY_DEFAULT_HSN_CODE).strip()


def multi_rate_product_codes(line_items):

    code_rates = {}

    for item in line_items or []:
        code = str(item.get("code") or "").strip()
        if not code:
            continue
        key = code.upper()
        rate = round(float(item.get("unit_price") or 0), 2)
        code_rates.setdefault(key, set()).add(rate)

    return {key for key, rates in code_rates.items() if len(rates) > 1}


def get_tally_stock_item_name(item, multi_rate_codes=None):

    # Prefer resolved Tally stock Name (matched by Part No).
    tally_name = str(item.get("tally_stock_name") or "").strip()
    if tally_name:
        return tally_name

    code = str(item.get("code") or "").strip()
    rate = float(item.get("unit_price") or 0)

    if code:
        if multi_rate_codes and code.upper() in multi_rate_codes and rate:
            return f"{code} @ {_tally_amount(rate)}"
        return code

    return str(item.get("product_name") or item.get("name") or "Product").strip()


def _normalize_part_no(value):

    return str(value or "").strip().upper().replace(" ", "")


def _stock_item_part_nos(item):

    """Part No in Tally is MAILINGNAME.LIST (enabled via Use Part Number)."""

    parts = []
    for node in item.findall(".//MAILINGNAME"):
        text = _clean_tally_text(node.text)
        if text:
            parts.append(text)

    for tag in ("MAILPARTNO", "PARTNO", "NOTES", "MAILINGNAME"):
        text = _clean_tally_text(item.findtext(tag) or "")
        if text and text not in parts:
            parts.append(text)

    return parts


def _parse_stock_item_node(item):

    name = _clean_tally_text(item.get("NAME") or item.findtext("NAME") or "")
    if not name:
        return None

    parts = _stock_item_part_nos(item)
    return {
        "name": name,
        "part_no": parts[0] if parts else "",
        "part_nos": parts,
        "parent": _clean_tally_text(item.findtext("PARENT")),
        "unit": _clean_tally_text(item.findtext("BASEUNITS")),
        "hsn": _stock_item_hsn_code(item),
    }


def _export_filtered_stock(formula_body, collection_id="StockByPartNo"):

    """Export a small filtered Stock Item collection. Never dump the full catalog."""

    static = _tally_static_variables("<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>")
    # Part No lives in MAILINGNAME.LIST; NATIVEMETHOD returns it reliably (FETCH often drops it).
    xml_request = f"""<ENVELOPE>
<HEADER>
<VERSION>1</VERSION>
<TALLYREQUEST>Export</TALLYREQUEST>
<TYPE>Collection</TYPE>
<ID>{escape(collection_id)}</ID>
</HEADER>
<BODY>
<DESC>
{static}
<TDL>
<TDLMESSAGE>
<COLLECTION NAME="{escape(collection_id)}" ISMODIFY="No">
<TYPE>Stock Item</TYPE>
<NATIVEMETHOD>NAME</NATIVEMETHOD>
<NATIVEMETHOD>MAILINGNAME</NATIVEMETHOD>
<NATIVEMETHOD>PARENT</NATIVEMETHOD>
<NATIVEMETHOD>BASEUNITS</NATIVEMETHOD>
<FILTER>PartFilter</FILTER>
</COLLECTION>
<SYSTEM TYPE="Formulae" NAME="PartFilter">{formula_body}</SYSTEM>
</TDLMESSAGE>
</TDL>
</DESC>
</BODY>
</ENVELOPE>"""

    response = _tally_post(xml_request)
    root = ET.fromstring(_sanitize_tally_xml(response.text))
    stocks = []
    for item in root.findall(".//STOCKITEM"):
        stock = _parse_stock_item_node(item)
        if stock:
            stocks.append(stock)
    return stocks


def find_tally_stock_by_part_no(part_no):

    """Look up ONE stock item by Part No (MAILINGNAME). Safe for Tally EDU."""

    code = str(part_no or "").strip()
    if not code:
        return None

    code_xml = escape(code)
    want = _normalize_part_no(code)

    # Confirmed on client company: Part No = MAILINGNAME; filter style `$MailingName = "..."`.
    filter_attempts = [
        f'$MailingName = "{code_xml}"',
        f'$PartNo = "{code_xml}"',
    ]

    for idx, formula in enumerate(filter_attempts):
        try:
            stocks = _export_filtered_stock(formula, collection_id=f"StockByPart{idx}")
        except (requests.RequestException, ET.ParseError):
            continue

        for stock in stocks:
            known = {_normalize_part_no(p) for p in (stock.get("part_nos") or []) if p}
            known.discard("")
            if want in known:
                stock["part_no"] = next(
                    (p for p in stock.get("part_nos") or [] if _normalize_part_no(p) == want),
                    code,
                )
                return stock
            # Exact MailingName/PartNo filter hit but list not exported — accept name match
            if stock.get("name"):
                stock["part_no"] = code
                return stock

    return None


def _parse_tally_rate_number(value):

    rate, _unit = _parse_tally_rate_string(value)
    return rate


def get_stock_sync_snapshot(part_no):

    """Read one Tally stock item by Part No for Salesforce sync.

    Safe filtered collection only (no CHILDOF / stock-name formulas).
    Returns name, rate, closing qty, and qty per mapped godown.
    """

    code = str(part_no or "").strip()
    if not code:
        return None

    code_xml = escape(code)
    want = _normalize_part_no(code)
    static = _tally_static_variables("<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>")

    # Godown closing via $$GodownClosingBalance:"Godown" — godown names are quoted;
    # never put stock item names into TDL formulas (hyphen → Bad formula).
    compute_tags = []
    compute_keys = {}
    for idx, (_wh_no, godown) in enumerate(sorted(TALLY_WAREHOUSE_GODOWN_MAP.items()), start=1):
        key = f"GdnQty{idx}"
        compute_keys[key.upper()] = godown
        compute_tags.append(
            f'<COMPUTE>{key} : $$GodownClosingBalance:"{escape(godown)}"</COMPUTE>'
        )
    compute_xml = "\n".join(compute_tags)

    xml_request = f"""<ENVELOPE>
<HEADER>
<VERSION>1</VERSION>
<TALLYREQUEST>Export</TALLYREQUEST>
<TYPE>Collection</TYPE>
<ID>StockSyncByPart</ID>
</HEADER>
<BODY>
<DESC>
{static}
<TDL>
<TDLMESSAGE>
<COLLECTION NAME="StockSyncByPart" ISMODIFY="No">
<TYPE>Stock Item</TYPE>
<NATIVEMETHOD>NAME</NATIVEMETHOD>
<NATIVEMETHOD>MAILINGNAME</NATIVEMETHOD>
<NATIVEMETHOD>CLOSINGBALANCE</NATIVEMETHOD>
<NATIVEMETHOD>OPENINGBALANCE</NATIVEMETHOD>
<NATIVEMETHOD>CLOSINGRATE</NATIVEMETHOD>
<NATIVEMETHOD>OPENINGRATE</NATIVEMETHOD>
<NATIVEMETHOD>BATCHALLOCATIONS</NATIVEMETHOD>
{compute_xml}
<FILTER>PartEq</FILTER>
</COLLECTION>
<SYSTEM TYPE="Formulae" NAME="PartEq">$MailingName = "{code_xml}"</SYSTEM>
</TDLMESSAGE>
</TDL>
</DESC>
</BODY>
</ENVELOPE>"""

    try:
        response = _tally_post(xml_request)
        root = ET.fromstring(_sanitize_tally_xml(response.text))
    except (requests.RequestException, ET.ParseError):
        return None

    item = None
    for node in root.findall(".//STOCKITEM"):
        stock = _parse_stock_item_node(node)
        if not stock:
            continue
        known = {_normalize_part_no(p) for p in (stock.get("part_nos") or []) if p}
        if want in known or (not known and stock.get("name")):
            item = node
            break

    if item is None:
        return None

    name = _clean_tally_text(item.get("NAME") or item.findtext("NAME") or "")
    closing_qty, _u = _parse_tally_qty_string(item.findtext("CLOSINGBALANCE"))
    opening_qty, _u2 = _parse_tally_qty_string(item.findtext("OPENINGBALANCE"))
    rate = (
        _parse_tally_rate_number(item.findtext("CLOSINGRATE"))
        or _parse_tally_rate_number(item.findtext("OPENINGRATE"))
        or 0.0
    )

    godowns = {}
    qty_source = "none"

    # Prefer COMPUTE closing fields when present
    for key, godown in compute_keys.items():
        raw = item.findtext(key) or item.findtext(key.title()) or ""
        if not str(raw).strip():
            continue
        qty, _ = _parse_tally_qty_string(raw)
        godowns[godown] = qty
        qty_source = "closing"

    # Fallback: batch opening balances (master allocations)
    if not godowns:
        for batch in item.findall(".//BATCHALLOCATIONS.LIST"):
            godown = _clean_tally_text(batch.findtext("GODOWNNAME") or "")
            if not godown:
                continue
            qty, _ = _parse_tally_qty_string(batch.findtext("OPENINGBALANCE"))
            godowns[godown] = godowns.get(godown, 0.0) + qty
            if not rate:
                rate = _parse_tally_rate_number(batch.findtext("OPENINGRATE")) or rate
        if godowns:
            qty_source = "opening_batch"

    return {
        "part_no": code,
        "name": name,
        "rate": float(rate or 0),
        "closing_qty": float(closing_qty or 0),
        "opening_qty": float(opening_qty or 0),
        "godowns": godowns,
        "qty_source": qty_source,
    }


def get_tally_stock_catalog():

    """Light Name-only stock list for /get-tally-products. Avoids Tally EDU crash."""

    static = _tally_static_variables("<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>")
    xml_request = f"""<ENVELOPE>
<HEADER>
<VERSION>1</VERSION>
<TALLYREQUEST>Export</TALLYREQUEST>
<TYPE>Collection</TYPE>
<ID>Stock Items</ID>
</HEADER>
<BODY>
<DESC>
{static}
<TDL>
<TDLMESSAGE>
<COLLECTION NAME="Stock Items" ISMODIFY="No">
<TYPE>Stock Item</TYPE>
<FETCH>Name</FETCH>
<FETCH>Parent</FETCH>
<FETCH>BaseUnits</FETCH>
</COLLECTION>
</TDLMESSAGE>
</TDL>
</DESC>
</BODY>
</ENVELOPE>"""

    response = _tally_post(xml_request)
    root = ET.fromstring(_sanitize_tally_xml(response.text))

    products = []
    for item in root.findall(".//STOCKITEM"):
        stock = _parse_stock_item_node(item)
        if stock:
            products.append(stock)
    return products


def match_line_items_to_tally_stock(line_items):

    """Match each Salesforce line code to an existing Tally stock Part No.

    Looks up one Part No at a time (safe for Tally EDU). Never creates stock.
    On success, each line gets tally_stock_name = Tally stock Name.
    Qty and rate stay from Salesforce.
    """

    items = [_normalize_line_item(item) for item in (line_items or [])]
    if not items:
        return {
            "success": False,
            "line_items": [],
            "missing_codes": [],
            "matched": [],
            "message": "No line items to match against Tally stock.",
        }

    cache = {}
    missing = []
    matched = []
    resolved = []

    for item in items:
        code = str(item.get("code") or "").strip()
        key = _normalize_part_no(code)

        if not key:
            missing.append("(blank code)")
            resolved.append(item)
            continue

        if key not in cache:
            cache[key] = find_tally_stock_by_part_no(code)

        stock = cache[key]
        if not stock:
            missing.append(code)
            resolved.append(item)
            continue

        resolved_item = {
            **item,
            "tally_stock_name": stock["name"],
            "tally_part_no": stock.get("part_no") or code,
        }
        resolved.append(resolved_item)
        matched.append({
            "code": code,
            "part_no": stock.get("part_no") or code,
            "tally_name": stock["name"],
            "quantity": item.get("quantity"),
            "unit_price": item.get("unit_price"),
        })

    if missing:
        unique_missing = sorted(set(missing))
        message = (
            "Stock item(s) not found in Tally (Part No mismatch). "
            f"Missing code(s): {', '.join(unique_missing)}. "
            "Create/update Part No in Tally stock item and retry. "
            "No stock items were created by the integration."
        )
        return {
            "success": False,
            "line_items": resolved,
            "missing_codes": unique_missing,
            "matched": matched,
            "message": message,
        }

    return {
        "success": True,
        "line_items": resolved,
        "missing_codes": [],
        "matched": matched,
        "message": f"Matched {len(matched)} line(s) to Tally stock by Part No.",
    }


def _inventory_description_tags(item):

    description = escape(str(item.get("description") or "").strip())
    if not description:
        return ""

    return f"""<BASICUSERDESCRIPTION.LIST TYPE="String">
<BASICUSERDESCRIPTION>{description}</BASICUSERDESCRIPTION>
</BASICUSERDESCRIPTION.LIST>
"""


def _inventory_hsn_tags(item):

    if not TALLY_ENABLE_GST:
        return ""

    hsn = escape(_item_hsn(item))
    description = escape(str(item.get("description") or "").strip())
    tags = f"""<GSTHSNNAME>{hsn}</GSTHSNNAME>
<GSTHSNINFERAPPLICABILITY>Specify Details Here</GSTHSNINFERAPPLICABILITY>
"""
    if description:
        tags += f"<GSTHSNDESCRIPTION>{description}</GSTHSNDESCRIPTION>\n"

    return tags


def _gst_rate_value(rate):

    rate = float(rate)
    return str(int(rate)) if rate == int(rate) else str(rate)


def _gst_rate_detail_block(duty_head, rate):

    rate_value = _gst_rate_value(rate)
    return f"""<RATEDETAILS.LIST>
<GSTRATEDUTYHEAD>{duty_head}</GSTRATEDUTYHEAD>
<GSTRATEVALUATIONTYPE>Based on Value</GSTRATEVALUATIONTYPE>
<GSTRATE>{rate_value}</GSTRATE>
<GSTRATEPERUNIT>0</GSTRATEPERUNIT>
</RATEDETAILS.LIST>"""


def _stock_gst_rate_blocks(bill_to=None):

    if bill_to and _is_intra_state_sale(bill_to):
        return (
            _gst_rate_detail_block("CGST", TALLY_CGST_RATE)
            + _gst_rate_detail_block("SGST/UTGST", TALLY_SGST_RATE)
        )

    return _gst_rate_detail_block("IGST", TALLY_IGST_RATE)


def _stock_gst_rate_blocks_full():

    return (
        _gst_rate_detail_block("CGST", TALLY_CGST_RATE)
        + _gst_rate_detail_block("SGST/UTGST", TALLY_SGST_RATE)
        + _gst_rate_detail_block("IGST", TALLY_IGST_RATE)
    )


def _stock_gst_details_full_xml():

    if not TALLY_ENABLE_GST:
        return ""

    return f"""<GSTDETAILS.LIST>
<APPLICABLEFROM>20240401</APPLICABLEFROM>
<TAXABILITY>Taxable</TAXABILITY>
<SRCOFGSTDETAILS>Specify Details Here</SRCOFGSTDETAILS>
<STATEWISEDETAILS.LIST>
<STATENAME>&#4; Any</STATENAME>
{_stock_gst_rate_blocks_full()}
<GSTSLABRATES.LIST>       </GSTSLABRATES.LIST>
</STATEWISEDETAILS.LIST>
</GSTDETAILS.LIST>"""


def _stock_gst_details_xml(bill_to=None):

    if not TALLY_ENABLE_GST:
        return ""

    return f"""<GSTDETAILS.LIST>
<APPLICABLEFROM>20240401</APPLICABLEFROM>
<TAXABILITY>Taxable</TAXABILITY>
<SRCOFGSTDETAILS>Specify Details Here</SRCOFGSTDETAILS>
<STATEWISEDETAILS.LIST>
<STATENAME>&#4; Any</STATENAME>
{_stock_gst_rate_blocks(bill_to)}
<GSTSLABRATES.LIST>       </GSTSLABRATES.LIST>
</STATEWISEDETAILS.LIST>
</GSTDETAILS.LIST>"""


def _inventory_gst_rate_tags(bill_to=None):

    if not TALLY_ENABLE_GST:
        return ""

    tags = """<GSTOVRDNTAXABILITY>Taxable</GSTOVRDNTAXABILITY>
<GSTRATEINFERAPPLICABILITY>Specify Details Here</GSTRATEINFERAPPLICABILITY>
"""
    return tags + _stock_gst_rate_blocks(bill_to)


def _use_gst_invoice_view():
    return TALLY_ENABLE_GST and TALLY_GST_INVOICE_VIEW


def _build_party_ledger_block(account_name, bill_name, party_amount_str, invoice_view=False):

    if invoice_view:
        return f"""<LEDGERENTRIES.LIST>
<LEDGERNAME>{account_name}</LEDGERNAME>
<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
<ISPARTYLEDGER>Yes</ISPARTYLEDGER>
<AMOUNT>-{party_amount_str}</AMOUNT>
<BILLALLOCATIONS.LIST>
<NAME>{bill_name}</NAME>
<BILLTYPE>New Ref</BILLTYPE>
<AMOUNT>-{party_amount_str}</AMOUNT>
</BILLALLOCATIONS.LIST>
</LEDGERENTRIES.LIST>"""

    return f"""<ALLLEDGERENTRIES.LIST>
<LEDGERNAME>{account_name}</LEDGERNAME>
<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
<ISPARTYLEDGER>Yes</ISPARTYLEDGER>
<AMOUNT>-{party_amount_str}</AMOUNT>
<BILLALLOCATIONS.LIST>
<NAME>{bill_name}</NAME>
<BILLTYPE>New Ref</BILLTYPE>
<AMOUNT>-{party_amount_str}</AMOUNT>
</BILLALLOCATIONS.LIST>
</ALLLEDGERENTRIES.LIST>"""


def _build_line_inventory_xml(
    item,
    godown_name=None,
    bill_to=None,
    multi_rate_codes=None,
    sales_ledger=DEFAULT_SALES_LEDGER,
):

    stock_name = escape(get_tally_stock_item_name(item, multi_rate_codes))
    hsn_tags = _inventory_hsn_tags(item)
    gst_tags = _inventory_gst_rate_tags(bill_to)
    description_tags = _inventory_description_tags(item)
    amount = _tally_amount(item["total_price"])
    qty = _tally_qty(item["quantity"])
    # Stock items track location (Ignore Godowns = No) — godown is required on batch lines.
    # Use config default / optional dispatch godown; do not create godowns here.
    godown = escape(str(godown_name or TALLY_GODOWN_NAME).strip() or TALLY_GODOWN_NAME)
    batch_block = f"""<BATCHALLOCATIONS.LIST>
<GODOWNNAME>{godown}</GODOWNNAME>
<BATCHNAME>{TALLY_BATCH_NAME}</BATCHNAME>
<AMOUNT>{amount}</AMOUNT>
<ACTUALQTY>{qty}</ACTUALQTY>
<BILLEDQTY>{qty}</BILLEDQTY>
</BATCHALLOCATIONS.LIST>"""
    accounting_block = _build_line_accounting_allocations(
        item,
        bill_to=bill_to,
        invoice_view=_use_gst_invoice_view(),
        sales_ledger=sales_ledger,
    )

    if _use_gst_invoice_view():
        return f"""<ALLINVENTORYENTRIES.LIST>
<STOCKITEMNAME>{stock_name}</STOCKITEMNAME>
<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
{description_tags}<RATE>{_tally_rate(item["unit_price"])}</RATE>
<AMOUNT>{amount}</AMOUNT>
<ACTUALQTY>{qty}</ACTUALQTY>
<BILLEDQTY>{qty}</BILLEDQTY>
{hsn_tags}{gst_tags}{batch_block}
{accounting_block}
</ALLINVENTORYENTRIES.LIST>
"""

    return f"""<INVENTORYALLOCATIONS.LIST>
<STOCKITEMNAME>{stock_name}</STOCKITEMNAME>
<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
{description_tags}<RATE>{_tally_rate(item["unit_price"])}</RATE>
<AMOUNT>{amount}</AMOUNT>
<ACTUALQTY>{qty}</ACTUALQTY>
<BILLEDQTY>{qty}</BILLEDQTY>
{hsn_tags}{gst_tags}{batch_block}
</INVENTORYALLOCATIONS.LIST>
"""


def consolidate_line_items(line_items):

    merged = {}

    for item in [_normalize_line_item(item) for item in line_items]:
        key = (
            item.get("code", ""),
            float(item.get("unit_price") or 0),
        )

        if key not in merged:
            merged[key] = {**item}
            continue

        merged[key]["quantity"] = float(merged[key]["quantity"] or 0) + float(item["quantity"] or 0)
        merged[key]["total_price"] = float(merged[key]["total_price"] or 0) + float(item["total_price"] or 0)

    return list(merged.values())


def balance_line_item_amounts(line_items, amount):

    items = [_normalize_line_item(item) for item in line_items]
    line_total = sum(float(item.get("total_price") or 0) for item in items)

    if line_total > 0:
        return items, line_total

    target_amount = float(amount or 0)
    if not target_amount:
        return items, 0

    total_qty = sum(float(item.get("quantity") or 0) for item in items)
    if not total_qty:
        total_qty = len(items) or 1

    balanced = []
    remaining = target_amount

    for index, item in enumerate(items):
        qty = float(item.get("quantity") or 0) or 1

        if index == len(items) - 1:
            line_amount = round(remaining, 2)
        else:
            line_amount = round(target_amount * (qty / total_qty), 2)
            remaining -= line_amount

        balanced.append({
            **item,
            "quantity": qty,
            "unit_price": line_amount / qty if qty else line_amount,
            "total_price": line_amount,
        })

    return balanced, target_amount


def extract_tally_error(response_text):

    import html

    for tag in ("LINEERROR", "EXCEPTION", "ERRMSG", "MESSAGE"):
        match = re.search(rf"<{tag}>(.*?)</{tag}>", response_text, re.DOTALL | re.IGNORECASE)
        if match and match.group(1).strip():
            return html.unescape(match.group(1).strip())

    if re.search(r"<EXCEPTIONS>\s*([1-9]\d*)\s*</EXCEPTIONS>", response_text):
        return None

    return None


def resolve_dispatch_godown(invoice_meta=None):

    """Map Salesforce Dispatch warehouse → existing Tally godown name.

    Does not create godowns. Warehouse is required:
      Warehouse 1 … → A Main Location
      Warehouse 2 … → Paniampalli
      Warehouse 3 … → Chennai Branch
    """

    warehouse = str((invoice_meta or {}).get("dispatch_from_name") or "").strip()
    if not warehouse:
        return {
            "success": False,
            "warehouse_name": "",
            "godown_name": None,
            "message": (
                "Warehouse is required on Dispatch. "
                "Set Warehouses__c (Warehouse 1 / 2 / 3) and retry. "
                "Invoice was not created."
            ),
        }

    match = re.match(r"^warehouse\s+(\d+)\b", warehouse, re.IGNORECASE)
    if not match:
        return {
            "success": False,
            "warehouse_name": warehouse,
            "godown_name": None,
            "message": (
                f"Warehouse '{warehouse}' is not mapped to a Tally godown. "
                "Use Warehouse 1, Warehouse 2, or Warehouse 3. Invoice was not created."
            ),
        }

    warehouse_no = match.group(1)
    godown_name = (TALLY_WAREHOUSE_GODOWN_MAP or {}).get(warehouse_no)
    if not godown_name:
        return {
            "success": False,
            "warehouse_name": warehouse,
            "godown_name": None,
            "message": (
                f"Warehouse {warehouse_no} ('{warehouse}') has no Tally godown mapping. "
                "Add it to TALLY_WAREHOUSE_GODOWN_MAP in config.py. Invoice was not created."
            ),
        }

    return {
        "success": True,
        "warehouse_name": warehouse,
        "godown_name": godown_name,
        "message": f"Warehouse '{warehouse}' → Tally godown '{godown_name}'",
    }


def create_godown(godown_name, location=None):

    godown_name = escape(godown_name)
    location = str(location or "").strip()
    address_xml = ""
    if location:
        address_xml = f"""
<ADDRESS.LIST TYPE="String">
<ADDRESS>{escape(location)}</ADDRESS>
</ADDRESS.LIST>"""

    xml = f"""<ENVELOPE>
<HEADER>
<TALLYREQUEST>Import Data</TALLYREQUEST>
</HEADER>
<BODY>
<IMPORTDATA>
{_import_requestdesc("All Masters")}
<REQUESTDATA>
<TALLYMESSAGE>
<GODOWN NAME="{godown_name}" ACTION="Create">
<NAME>{godown_name}</NAME>
{address_xml}
</GODOWN>
</TALLYMESSAGE>
</REQUESTDATA>
</IMPORTDATA>
</BODY>
</ENVELOPE>"""

    print("=" * 70)
    print(f"Ensuring Godown : {godown_name}")
    print("=" * 70)

    response = _tally_post(xml)

    return response.text


def create_tax_ledger(ledger_name, duty_head):

    ledger_name = escape(ledger_name)
    duty_head = escape(duty_head)

    xml = f"""<ENVELOPE>
<HEADER>
<TALLYREQUEST>Import Data</TALLYREQUEST>
</HEADER>
<BODY>
<IMPORTDATA>
{_import_requestdesc("All Masters")}
<REQUESTDATA>
<TALLYMESSAGE>
<LEDGER NAME="{ledger_name}" ACTION="Alter">
<NAME>{ledger_name}</NAME>
<PARENT>Duties &amp; Taxes</PARENT>
<TAXTYPE>GST</TAXTYPE>
<GSTDUTYHEAD>{duty_head}</GSTDUTYHEAD>
<RATEOFTAXCALCULATION>0</RATEOFTAXCALCULATION>
<ISGSTAPPLICABLE>Yes</ISGSTAPPLICABLE>
<GSTTYPEOFSUPPLY>Goods</GSTTYPEOFSUPPLY>
</LEDGER>
</TALLYMESSAGE>
</REQUESTDATA>
</IMPORTDATA>
</BODY>
</ENVELOPE>"""

    response = _tally_post(xml)

    return response.text


def ensure_round_off_ledger():

    if not TALLY_ENABLE_ROUND_OFF:
        return None

    ledger_name = escape(TALLY_ROUND_OFF_LEDGER)
    parent = escape(TALLY_ROUND_OFF_PARENT)
    rounding_method = escape(TALLY_ROUNDING_METHOD)
    xml = f"""<ENVELOPE>
<HEADER>
<TALLYREQUEST>Import Data</TALLYREQUEST>
</HEADER>
<BODY>
<IMPORTDATA>
{_import_requestdesc("All Masters")}
<REQUESTDATA>
<TALLYMESSAGE>
<LEDGER NAME="{ledger_name}" ACTION="Alter">
<NAME>{ledger_name}</NAME>
<PARENT>{parent}</PARENT>
<ISLEDGERINFORINVOICEROUNDING>Yes</ISLEDGERINFORINVOICEROUNDING>
<ROUNDINGMETHOD>{rounding_method}</ROUNDINGMETHOD>
<ROUNDINGLIMIT>{int(TALLY_ROUNDING_LIMIT)}</ROUNDINGLIMIT>
</LEDGER>
</TALLYMESSAGE>
</REQUESTDATA>
</IMPORTDATA>
</BODY>
</ENVELOPE>"""

    print("\nEnsuring Round Off Ledger (Invoice Rounding)")
    print("=" * 70)

    response = _tally_post(xml).text

    if "<ALTERED>0</ALTERED>" in response and "<CREATED>0</CREATED>" in response:
        create_xml = xml.replace('ACTION="Alter"', 'ACTION="Create"')
        response = _tally_post(create_xml).text

    return response


def ensure_sales_ledger():

    xml = """<ENVELOPE>
<HEADER>
<TALLYREQUEST>Import Data</TALLYREQUEST>
</HEADER>
<BODY>
<IMPORTDATA>
{_import_requestdesc("All Masters")}
<REQUESTDATA>
<TALLYMESSAGE>
<LEDGER NAME="Sales" ACTION="Alter">
<NAME>Sales</NAME>
<PARENT>Sales Accounts</PARENT>
<GSTAPPLICABLE>&#4; Applicable</GSTAPPLICABLE>
<GSTTYPEOFSUPPLY>Goods</GSTTYPEOFSUPPLY>
</LEDGER>
</TALLYMESSAGE>
</REQUESTDATA>
</IMPORTDATA>
</BODY>
</ENVELOPE>"""

    print("\nEnsuring Sales Ledger GST setup")
    print("=" * 70)

    return _tally_post(xml).text


def ensure_company_gst_registration():

    if not TALLY_ENABLE_GST or not TALLY_COMPANY_GSTIN:
        return None

    state = escape(TALLY_COMPANY_STATE)
    gstin = escape(TALLY_COMPANY_GSTIN)

    xml = f"""<ENVELOPE>
<HEADER>
<TALLYREQUEST>Import Data</TALLYREQUEST>
</HEADER>
<BODY>
<IMPORTDATA>
{_import_requestdesc("All Masters")}
<REQUESTDATA>
<TALLYMESSAGE>
<GSTREGISTRATION ACTION="Alter">
<NAME>{state}</NAME>
<APPLICABLEFROM>20240401</APPLICABLEFROM>
<GSTREGISTRATIONTYPE>Regular</GSTREGISTRATIONTYPE>
<STATE>{state}</STATE>
<GSTIN>{gstin}</GSTIN>
<PLACEOFSUPPLY>{state}</PLACEOFSUPPLY>
</GSTREGISTRATION>
</TALLYMESSAGE>
</REQUESTDATA>
</IMPORTDATA>
</BODY>
</ENVELOPE>"""

    print("\nEnsuring Company GST Registration")
    print("=" * 70)

    return _tally_post(xml).text


def ensure_tax_ledgers(bill_to=None):

    """Optional one-time setup helper for test scripts — not run on normal invoices.

    Configure CGST / SGST / IGST ledgers manually in Tally instead.
    """
    if not TALLY_ENABLE_GST:
        return []

    if _is_intra_state_sale(bill_to):
        ledgers = [
            (TALLY_CGST_LEDGER, "Central Tax"),
            (TALLY_SGST_LEDGER, "State Tax"),
        ]
    else:
        ledgers = [(TALLY_IGST_LEDGER, "Integrated Tax")]

    print("\nEnsuring Tax Ledgers")
    print("=" * 70)

    results = []
    for name, tax_type in ledgers:
        response = create_tax_ledger(name, tax_type)
        print(f"Tax Ledger : {name}")
        print(response)
        results.append({"name": name, "response": response})

    return results


def ensure_dispatch_location(invoice_meta=None):

    """Resolve warehouse → godown only. Never creates godowns."""

    resolved = resolve_dispatch_godown(invoice_meta)
    return {
        "success": resolved.get("success"),
        "godown_name": resolved.get("godown_name"),
        "warehouse_name": resolved.get("warehouse_name"),
        "message": resolved.get("message"),
        "response": None,
    }


# ==========================================
# Create Ledger
# ==========================================

def _format_postal(value):
    if value in (None, ""):
        return ""
    return str(value).split(".")[0]


def _split_address_lines(bill_to):
    street = str(bill_to.get("street") or "").strip()
    city = str(bill_to.get("city") or "").strip()
    state = str(bill_to.get("state") or "").strip()
    country = str(bill_to.get("country") or "").strip()
    postal = _format_postal(bill_to.get("postal"))

    lines = []
    if street:
        lines.append(street)
    city_line = ", ".join(v for v in [city, state, postal] if v)
    if city_line:
        lines.append(city_line)
    if country:
        lines.append(country)
    return lines


def create_ledger(customer_name, bill_to=None):

    customer_name = escape(customer_name)

    bill_to = bill_to or {}
    mailing_name = escape(str(bill_to.get("name") or customer_name))
    state = escape(str(bill_to.get("state") or ""))
    country = escape(str(bill_to.get("country") or "India"))
    postal = escape(_format_postal(bill_to.get("postal")))
    gstin = escape(str(bill_to.get("gstin") or ""))
    gst_registration_type = "Regular" if gstin else "Unregistered/Consumer"

    address_xml = "".join(
        f"<ADDRESS>{escape(str(line))}</ADDRESS>"
        for line in _split_address_lines(bill_to)
    )

    mailing_block = f"""<MAILINGNAME.LIST TYPE="String">
<MAILINGNAME>{mailing_name}</MAILINGNAME>
</MAILINGNAME.LIST>
<ADDRESS.LIST TYPE="String">
{address_xml}
</ADDRESS.LIST>
<PINCODE>{postal}</PINCODE>
<COUNTRYNAME>{country}</COUNTRYNAME>
<COUNTRYOFRESIDENCE>{country}</COUNTRYOFRESIDENCE>
<LEDSTATENAME>{state}</LEDSTATENAME>
<STATENAME>{state}</STATENAME>
<LEDMAILINGDETAILS.LIST>
<APPLICABLEFROM>20230401</APPLICABLEFROM>
<MAILINGNAME>{mailing_name}</MAILINGNAME>
<ADDRESS.LIST>
{address_xml}
</ADDRESS.LIST>
<STATE>{state}</STATE>
<COUNTRY>{country}</COUNTRY>
<PINCODE>{postal}</PINCODE>
</LEDMAILINGDETAILS.LIST>"""

    pan = escape(str(bill_to.get("pan") or "").strip())
    pan_block = f"<INCOMETAXNUMBER>{pan}</INCOMETAXNUMBER>" if pan else ""

    gst_block = ""
    if gstin:
        gst_block = f"""<PARTYGSTIN>{gstin}</PARTYGSTIN>
<GSTIN>{gstin}</GSTIN>
<GSTREGISTRATIONTYPE>{gst_registration_type}</GSTREGISTRATIONTYPE>
<LEDGSTREGDETAILS.LIST>
<APPLICABLEFROM>20230401</APPLICABLEFROM>
<GSTREGISTRATIONTYPE>{gst_registration_type}</GSTREGISTRATIONTYPE>
<STATE>{state}</STATE>
<GSTIN>{gstin}</GSTIN>
<PLACEOFSUPPLY>{state}</PLACEOFSUPPLY>
</LEDGSTREGDETAILS.LIST>"""

    xml = f"""
    <ENVELOPE>
      <HEADER>
        <TALLYREQUEST>Import Data</TALLYREQUEST>
      </HEADER>
      <BODY>
        <IMPORTDATA>
          {_import_requestdesc("All Masters")}
          <REQUESTDATA>
            <TALLYMESSAGE>
              <LEDGER NAME="{customer_name}" ACTION="Alter">
                <NAME>{customer_name}</NAME>
                <PARENT>Sundry Debtors</PARENT>
                {mailing_block}
                <ISBILLWISEON>Yes</ISBILLWISEON>
                {pan_block}
                {gst_block}
              </LEDGER>
            </TALLYMESSAGE>
          </REQUESTDATA>
        </IMPORTDATA>
      </BODY>
    </ENVELOPE>
    """
    print("=" * 70)
    print(f"Updating Ledger : {customer_name}")
    print(xml)
    print("=" * 70)

    response = _tally_post(xml)

    return response.text


# ==========================================
# Create Voucher
# ==========================================
def parse_voucher_type_option(value):

    """Parse Invoice_Voucher_Type__c which may be a combined value:

        "Group / Sales Ledger / Voucher Type"
        e.g. "GST Sales - HO / Branch Sales / HO-Branch Sales"

    Returns {"group", "sales_ledger", "voucher_type"}.
    Backward compatible with a plain voucher type (no "/"),
    e.g. "Unit - 1" -> voucher_type only, default Sales ledger.
    """

    raw = str(value or "").strip()
    if not raw:
        return {"group": "", "sales_ledger": DEFAULT_SALES_LEDGER, "voucher_type": ""}

    parts = [part.strip() for part in raw.split("/")]
    parts = [part for part in parts if part]

    if len(parts) >= 3:
        return {
            "group": parts[0],
            "sales_ledger": parts[1],
            "voucher_type": parts[2],
        }
    if len(parts) == 2:
        return {
            "group": "",
            "sales_ledger": parts[0],
            "voucher_type": parts[1],
        }
    return {
        "group": "",
        "sales_ledger": DEFAULT_SALES_LEDGER,
        "voucher_type": parts[0],
    }


def _resolve_voucher_type(voucher_type=None):

    name = str(voucher_type or "").strip()
    if not name:
        raise ValueError(
            "Invoice voucher type is required. Set Invoice_Voucher_Type__c on Dispatch."
        )
    return escape(name)


def _build_voucher_narration(created_by=None):

    created_by_name = str(created_by or "").strip()
    if not created_by_name:
        return ""
    return f"Created By : {escape(created_by_name)}"


def create_sales_voucher(
    account_name,
    amount,
    reference_name,
    reference_id,
    voucher_date,
    line_items=None,
    source_label="Salesforce",
    bill_reference=None,
    invoice_meta=None,
    bill_to=None,
    voucher_type=None,
    created_by=None,
):

    account_name = escape(account_name)
    bill_name = escape(bill_reference or reference_name)
    invoice_meta = invoice_meta or {}
    bill_to = bill_to or {}

    option = parse_voucher_type_option(voucher_type)
    voucher_type_name = _resolve_voucher_type(option["voucher_type"])
    sales_ledger_name = str(option["sales_ledger"] or DEFAULT_SALES_LEDGER).strip()
    sales_ledger_xml = escape(sales_ledger_name)
    if option["group"]:
        print(
            f"Voucher Option -> Group: {option['group']}, "
            f"Sales Ledger: {sales_ledger_name}, "
            f"Voucher Type: {option['voucher_type']}"
        )

    inventory_entries = ""
    sales_inventory_allocations = ""
    normalized_items = []

    if line_items:
        normalized_items = [_normalize_line_item(item) for item in line_items]
        multi_rate_codes = multi_rate_product_codes(normalized_items)
        # Prefer godown already resolved in main (warehouse → Tally). Fallback resolves again.
        godown_for_lines = str(invoice_meta.get("tally_godown_name") or "").strip()
        if not godown_for_lines:
            godown_resolved = resolve_dispatch_godown(invoice_meta)
            if not godown_resolved.get("success"):
                raise ValueError(
                    godown_resolved.get("message")
                    or "Warehouse / godown mapping failed."
                )
            godown_for_lines = godown_resolved["godown_name"]
            print(godown_resolved.get("message"))
        else:
            print(f"Tally godown: {godown_for_lines}")

        if TALLY_VOUCHER_MODE == "item_invoice":
            for item in normalized_items:
                line_xml = _build_line_inventory_xml(
                    item,
                    godown_name=godown_for_lines,
                    bill_to=bill_to,
                    multi_rate_codes=multi_rate_codes,
                    sales_ledger=sales_ledger_name,
                )
                if _use_gst_invoice_view():
                    inventory_entries += line_xml
                else:
                    sales_inventory_allocations += line_xml
        else:
            for item in normalized_items:
                stock_name = escape(
                    get_tally_stock_item_name(item, multi_rate_codes)
                )
                hsn_tags = _inventory_hsn_tags(item)
                description_tags = _inventory_description_tags(item)
                godown = escape(godown_for_lines)
                inventory_entries += f"""<ALLINVENTORYENTRIES.LIST>
<STOCKITEMNAME>{stock_name}</STOCKITEMNAME>
<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
{description_tags}<RATE>{_tally_rate(item["unit_price"])}</RATE>
<AMOUNT>{_tally_amount(item["total_price"])}</AMOUNT>
<ACTUALQTY>{_tally_qty(item["quantity"])}</ACTUALQTY>
<BILLEDQTY>{_tally_qty(item["quantity"])}</BILLEDQTY>
{hsn_tags}<BATCHALLOCATIONS.LIST>
<GODOWNNAME>{godown}</GODOWNNAME>
<BATCHNAME>{TALLY_BATCH_NAME}</BATCHNAME>
<AMOUNT>{_tally_amount(item["total_price"])}</AMOUNT>
<ACTUALQTY>{_tally_qty(item["quantity"])}</ACTUALQTY>
<BILLEDQTY>{_tally_qty(item["quantity"])}</BILLEDQTY>
</BATCHALLOCATIONS.LIST>
</ALLINVENTORYENTRIES.LIST>
"""

    if normalized_items:
        amount_str = _tally_amount(
            sum(float(item["total_price"]) for item in normalized_items)
        )
    else:
        amount_str = _tally_amount(amount)

    taxable_amount = float(amount_str)
    invoice_view = (
        _use_gst_invoice_view()
        and TALLY_VOUCHER_MODE == "item_invoice"
        and bool(normalized_items)
    )

    if invoice_view:
        tax_ledger_entries, party_total = _build_tax_ledger_entries(
            taxable_amount, bill_to, invoice_view=True
        )
    else:
        tax_ledger_entries, party_total = _build_tax_ledger_entries(taxable_amount, bill_to)

    round_off_entries = ""
    if TALLY_ENABLE_ROUND_OFF:
        ensure_round_off_ledger()
        party_total, round_off_amount = _round_to_nearest_rupee(party_total)
        round_off_entries = _build_round_off_ledger_entry(
            round_off_amount,
            invoice_view=invoice_view,
        )

    party_amount_str = _tally_amount(party_total)

    if normalized_items:
        if TALLY_VOUCHER_MODE == "item_invoice":
            if _use_gst_invoice_view():
                voucher_open = (
                    f'<VOUCHER VCHTYPE="{voucher_type_name}" ACTION="Create" '
                    f'OBJVIEW="Invoice Voucher View">'
                )
                invoice_tags = """<PERSISTEDVIEW>Invoice Voucher View</PERSISTEDVIEW>
<ISINVOICE>Yes</ISINVOICE>
<VCHENTRYMODE>Item Invoice</VCHENTRYMODE>"""
                sales_ledger_entry = ""
            else:
                voucher_open = (
                    f'<VOUCHER VCHTYPE="{voucher_type_name}" ACTION="Create" '
                    f'OBJVIEW="Accounting Voucher View">'
                )
                invoice_tags = "<PERSISTEDVIEW>Accounting Voucher View</PERSISTEDVIEW>"
                sales_ledger_entry = f"""<ALLLEDGERENTRIES.LIST>
<LEDGERNAME>{sales_ledger_xml}</LEDGERNAME>
<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
<AMOUNT>{amount_str}</AMOUNT>
{sales_inventory_allocations}</ALLLEDGERENTRIES.LIST>"""
        else:
            voucher_open = (
                f'<VOUCHER VCHTYPE="{voucher_type_name}" ACTION="Create" '
                f'OBJVIEW="Accounting Voucher View">'
            )
            invoice_tags = """<PERSISTEDVIEW>Accounting Voucher View</PERSISTEDVIEW>
<USEFORINVENTORY>Yes</USEFORINVENTORY>"""
            sales_ledger_entry = f"""<ALLLEDGERENTRIES.LIST>
<LEDGERNAME>{sales_ledger_xml}</LEDGERNAME>
<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
<AMOUNT>{amount_str}</AMOUNT>
</ALLLEDGERENTRIES.LIST>"""
    else:
        voucher_open = (
            f'<VOUCHER ACTION="Create" VCHTYPE="{voucher_type_name}">'
        )
        invoice_tags = ""
        sales_ledger_entry = f"""<ALLLEDGERENTRIES.LIST>
<LEDGERNAME>{sales_ledger_xml}</LEDGERNAME>
<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
<AMOUNT>{amount_str}</AMOUNT>
</ALLLEDGERENTRIES.LIST>"""

    buyer_name = escape(str(bill_to.get("name") or account_name))
    buyer_state = escape(str(bill_to.get("state") or ""))
    buyer_country = escape(str(bill_to.get("country") or ""))
    buyer_postal = escape(_format_postal(bill_to.get("postal")))
    buyer_gstin = escape(str(bill_to.get("gstin") or ""))
    buyer_address_lines = _split_address_lines(bill_to)
    buyer_address = escape(", ".join(buyer_address_lines))
    buyer_address_xml = "".join(
        f"<ADDRESS>{escape(line)}</ADDRESS>"
        for line in buyer_address_lines
    )

    buyer_tags = f"""<BASICBASEPARTYNAME>{buyer_name}</BASICBASEPARTYNAME>
<PARTYMAILINGNAME>{buyer_name}</PARTYMAILINGNAME>
<BASICBUYERNAME>{buyer_name}</BASICBUYERNAME>"""
    if buyer_address_xml:
        buyer_tags += f"""
<ADDRESS.LIST TYPE="String">
{buyer_address_xml}
</ADDRESS.LIST>"""
    if buyer_address:
        buyer_tags += f"""
<BASICBUYERADDRESS.LIST TYPE="String">
<BASICBUYERADDRESS>{buyer_address}</BASICBUYERADDRESS>
</BASICBUYERADDRESS.LIST>"""
    if buyer_state:
        buyer_tags += f"\n<STATENAME>{buyer_state}</STATENAME>"
    if buyer_country:
        buyer_tags += f"\n<COUNTRYOFRESIDENCE>{buyer_country}</COUNTRYOFRESIDENCE>"
    if buyer_postal:
        buyer_tags += f"\n<PARTYPINCODE>{buyer_postal}</PARTYPINCODE>"
    if buyer_gstin:
        buyer_tags += f"\n<PARTYGSTIN>{buyer_gstin}</PARTYGSTIN>"
    if TALLY_ENABLE_GST and buyer_state:
        buyer_tags += f"\n<PLACEOFSUPPLY>{buyer_state}</PLACEOFSUPPLY>"
    if TALLY_ENABLE_GST and invoice_view:
        buyer_tags += "\n<GSTREGISTRATIONTYPE>Regular</GSTREGISTRATIONTYPE>"
    if TALLY_ENABLE_GST and TALLY_COMPANY_GSTIN:
        company_state = escape(TALLY_COMPANY_STATE)
        company_gstin = escape(TALLY_COMPANY_GSTIN)
        buyer_tags += f"""
<CMPGSTIN>{company_gstin}</CMPGSTIN>
<CMPGSTSTATE>{company_state}</CMPGSTSTATE>
<CMPGSTREGISTRATIONTYPE>Regular</CMPGSTREGISTRATIONTYPE>
<GSTREGISTRATION>{company_gstin}</GSTREGISTRATION>"""

    consignee_name = escape(str(invoice_meta.get("consignee") or ""))
    consignee_gstin = escape(str(invoice_meta.get("shipping_gstin") or "").strip())
    ship_to = {
        "street": invoice_meta.get("shipping_street"),
        "city": invoice_meta.get("shipping_city"),
        "state": invoice_meta.get("shipping_state"),
        "country": invoice_meta.get("shipping_country"),
        "postal": invoice_meta.get("shipping_postal"),
    }
    ship_street = str(ship_to.get("street") or "").strip()
    ship_city = str(ship_to.get("city") or "").strip()
    ship_state = str(ship_to.get("state") or "").strip()
    ship_country = str(ship_to.get("country") or "").strip()
    ship_postal = _format_postal(ship_to.get("postal"))
    ship_address_lines = _split_address_lines(ship_to)
    ship_address = escape(", ".join(ship_address_lines))
    ship_address_xml = "".join(
        f"<ADDRESS>{escape(line)}</ADDRESS>"
        for line in ship_address_lines
    )
    destination_value = ", ".join(v for v in [ship_city, ship_state, ship_country] if v)
    dispatch_destination = str(invoice_meta.get("destination") or "").strip()
    if dispatch_destination:
        destination_value = dispatch_destination

    dispatch_from_raw_name = str(invoice_meta.get("dispatch_from_name") or "").strip()
    dispatch_from_raw_location = str(invoice_meta.get("dispatch_from_location") or "").strip()
    dispatch_from_name = escape(dispatch_from_raw_name)
    dispatch_from_location = escape(dispatch_from_raw_location)
    dispatch_from_lines = [
        line for line in [dispatch_from_raw_name, dispatch_from_raw_location] if line
    ]
    dispatch_from_tags = ""
    if dispatch_from_name:
        dispatch_from_tags += f"<DISPATCHFROMNAME>{dispatch_from_name}</DISPATCHFROMNAME>\n"
    if dispatch_from_raw_location:
        dispatch_from_tags += f"<DISPATCHFROMPLACE>{dispatch_from_location}</DISPATCHFROMPLACE>\n"
    if dispatch_from_lines:
        dispatch_from_tags += '<DISPATCHFROMADDRESS.LIST TYPE="String">\n'
        for line in dispatch_from_lines:
            dispatch_from_tags += f"<DISPATCHFROMADDRESS>{escape(line)}</DISPATCHFROMADDRESS>\n"
        dispatch_from_tags += "</DISPATCHFROMADDRESS.LIST>\n"

    vehicle_number = escape(str(invoice_meta.get("vehicle_number") or "").strip())
    freight_terms = escape(str(invoice_meta.get("freight") or "").strip())

    # Tally invoice print fields (see TallyHelp import field aliases):
    # Motor Vehicle No. -> BASICSHIPVESSELNO
    # Destination -> BASICFINALDESTINATION
    # Terms of Delivery / Freight -> BASICORDERTERMS
    transport_tags = ""
    if dispatch_from_name:
        transport_tags += f"<BASICSHIPPEDBY>{dispatch_from_name}</BASICSHIPPEDBY>\n"
    if vehicle_number:
        transport_tags += f"<BASICSHIPVESSELNO>{vehicle_number}</BASICSHIPVESSELNO>\n"
    if destination_value:
        destination_escaped = escape(destination_value)
        transport_tags += f"<BASICFINALDESTINATION>{destination_escaped}</BASICFINALDESTINATION>\n"
    if freight_terms:
        transport_tags += f"<BASICORDERTERMS>{freight_terms}</BASICORDERTERMS>\n"

    consignee_tags = ""
    if consignee_name:
        consignee_tags += f"<CONSIGNEEMAILINGNAME>{consignee_name}</CONSIGNEEMAILINGNAME>\n"
    if ship_address_xml:
        consignee_tags += f"""<CONSIGNEEADDRESS.LIST TYPE="String">
{ship_address_xml}
</CONSIGNEEADDRESS.LIST>
"""
    if ship_state:
        consignee_tags += f"<CONSIGNEESTATENAME>{escape(ship_state)}</CONSIGNEESTATENAME>\n"
    if ship_postal:
        consignee_tags += f"<CONSIGNEEPINCODE>{escape(ship_postal)}</CONSIGNEEPINCODE>\n"
    if ship_country:
        consignee_tags += f"<CONSIGNEECOUNTRYNAME>{escape(ship_country)}</CONSIGNEECOUNTRYNAME>\n"
    if consignee_gstin:
        consignee_tags += f"<CONSIGNEEGSTIN>{consignee_gstin}</CONSIGNEEGSTIN>\n"

    party_ledger_block = _build_party_ledger_block(
        account_name,
        bill_name,
        party_amount_str,
        invoice_view=invoice_view,
    )

    xml = f"""<ENVELOPE>
<HEADER>
<TALLYREQUEST>Import Data</TALLYREQUEST>
</HEADER>
<BODY>
<IMPORTDATA>
{_import_requestdesc("Vouchers")}
<REQUESTDATA>
<TALLYMESSAGE>
{voucher_open}
<DATE>{voucher_date}</DATE>
{invoice_tags}
<VOUCHERTYPENAME>{voucher_type_name}</VOUCHERTYPENAME>
<PARTYLEDGERNAME>{account_name}</PARTYLEDGERNAME>
<NARRATION>{_build_voucher_narration(created_by)}</NARRATION>
{buyer_tags}
{consignee_tags}
{dispatch_from_tags}
{transport_tags}
{party_ledger_block}
{inventory_entries}
{sales_ledger_entry}
{tax_ledger_entries}{round_off_entries}</VOUCHER>
</TALLYMESSAGE>
</REQUESTDATA>
</IMPORTDATA>
</BODY>
</ENVELOPE>"""
    print("=" * 70)
    print(f"Creating Voucher for : {reference_name} ({voucher_type_name})")
    print(xml)
    print("=" * 70)

    response = _tally_post(xml)

    return response.text


# ==========================================
# create stock item
# ==========================================

def create_stock_item(
        product_name,
        product_code,
        opening_qty=100000,
        godown_name=None,
        description=None,
        hsn_code=None):

    opening_qty = max(int(float(opening_qty or 0)), 100000)
    godown_name = escape(godown_name or TALLY_GODOWN_NAME)
    product_name = escape(product_name)
    product_code = escape(product_code or "")
    description = escape(str(description or "").strip())
    hsn_code = escape(str(hsn_code or TALLY_DEFAULT_HSN_CODE).strip())

    description_xml = ""
    if description:
        description_xml = f"<DESCRIPTION>{description}</DESCRIPTION>"

    hsn_description_xml = (
        f"<DESCRIPTION>{description}</DESCRIPTION>"
        if description
        else ""
    )

    gst_xml = f"""<GSTAPPLICABLE>&#4; Applicable</GSTAPPLICABLE>
<GSTTYPEOFSUPPLY>Goods</GSTTYPEOFSUPPLY>
<HSNDETAILS.LIST>
<APPLICABLEFROM>20240401</APPLICABLEFROM>
<HSNCODE>{hsn_code}</HSNCODE>
{hsn_description_xml}
<SRCOFHSNDETAILS>Specify Details Here</SRCOFHSNDETAILS>
</HSNDETAILS.LIST>
{_stock_gst_details_full_xml()}"""

    xml = f"""
    <ENVELOPE>
    <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
    </HEADER>

    <BODY>
    <IMPORTDATA>

    {_import_requestdesc("All Masters")}

    <REQUESTDATA>

        <TALLYMESSAGE>

        <STOCKITEM NAME="{product_name}" ACTION="Alter">

        <NAME>{product_name}</NAME>

        <BASEUNITS>Nos</BASEUNITS>

        <OPENINGBALANCE>{opening_qty} Nos</OPENINGBALANCE>

        <OPENINGVALUE>{opening_qty}.00</OPENINGVALUE>

        {description_xml}

        {gst_xml}

        <BATCHALLOCATIONS.LIST>
        <GODOWNNAME>{godown_name}</GODOWNNAME>
        <BATCHNAME>Primary Batch</BATCHNAME>
        <OPENINGBALANCE>{opening_qty} Nos</OPENINGBALANCE>
        </BATCHALLOCATIONS.LIST>

        <NOTES>{product_code}</NOTES>

        </STOCKITEM>

        </TALLYMESSAGE>

    </REQUESTDATA>

    </IMPORTDATA>
    </BODY>
    </ENVELOPE>
    """

    response = _tally_post(xml)

    print("=" * 70)
    print(f"Stock Item : {product_name}")
    print("=" * 70)
    print(response.text)

    return response.text


def update_tally_stock_from_salesforce(
    stock_name,
    part_no,
    godown_qty,
    rate=0,
    batch_name=None,
):

    """Alter an existing Tally stock item from Salesforce qty/rate.

    Sets OpeningBalance + OpeningRate + OpeningValue on the item and on every
    godown BATCHALLOCATIONS line (allocation popup Rate / Amount).
    Does not create new stock items.
    """

    stock_name = str(stock_name or "").strip()
    if not stock_name:
        raise ValueError("stock_name is required")

    part_no = str(part_no or "").strip()
    batch_name = escape(str(batch_name or TALLY_BATCH_NAME).strip() or TALLY_BATCH_NAME)
    name_xml = escape(stock_name)
    part_xml = escape(part_no)
    rate_num = float(rate or 0)

    allocations = []
    total_qty = 0.0
    total_abs_value = 0.0
    for godown, qty in (godown_qty or {}).items():
        godown = str(godown or "").strip()
        if not godown:
            continue
        qty_num = float(qty or 0)
        total_qty += qty_num
        qty_str = _tally_qty(qty_num)
        if qty_num < 0:
            qty_str = f"-{_tally_qty(abs(qty_num))}"

        # Per-godown Rate / Amount (required for Stock Item Allocation popup)
        line_rate_xml = ""
        line_value_xml = ""
        if rate_num > 0:
            # Tally stock opening value is typically -(qty × rate)
            line_value = -(qty_num * rate_num)
            total_abs_value += abs(qty_num * rate_num)
            line_rate_xml = f"<OPENINGRATE>{_tally_rate(rate_num)}</OPENINGRATE>\n"
            line_value_xml = f"<OPENINGVALUE>{_tally_amount(line_value)}</OPENINGVALUE>\n"

        allocations.append(
            f"""<BATCHALLOCATIONS.LIST>
<GODOWNNAME>{escape(godown)}</GODOWNNAME>
<BATCHNAME>{batch_name}</BATCHNAME>
<OPENINGBALANCE>{qty_str} Nos</OPENINGBALANCE>
{line_rate_xml}{line_value_xml}</BATCHALLOCATIONS.LIST>"""
        )

    if not allocations:
        raise ValueError(f"No godown quantities provided for {stock_name}")

    rate_xml = ""
    value_xml = ""
    if rate_num > 0:
        rate_xml = f"<OPENINGRATE>{_tally_rate(rate_num)}</OPENINGRATE>\n"
        # Header opening value = -(total_qty × rate)
        value_xml = f"<OPENINGVALUE>{_tally_amount(-(total_qty * rate_num))}</OPENINGVALUE>\n"

    total_qty_str = _tally_qty(total_qty)
    if total_qty < 0:
        total_qty_str = f"-{_tally_qty(abs(total_qty))}"

    mailing_xml = ""
    if part_xml:
        mailing_xml = f"""<MAILINGNAME.LIST TYPE="String">
<MAILINGNAME>{part_xml}</MAILINGNAME>
</MAILINGNAME.LIST>
"""

    xml = f"""<ENVELOPE>
<HEADER>
<TALLYREQUEST>Import Data</TALLYREQUEST>
</HEADER>
<BODY>
<IMPORTDATA>
{_import_requestdesc("All Masters")}
<REQUESTDATA>
<TALLYMESSAGE>
<STOCKITEM NAME="{name_xml}" ACTION="Alter">
<NAME>{name_xml}</NAME>
{mailing_xml}<BASEUNITS>Nos</BASEUNITS>
<OPENINGBALANCE>{total_qty_str} Nos</OPENINGBALANCE>
{rate_xml}{value_xml}{"".join(allocations)}
</STOCKITEM>
</TALLYMESSAGE>
</REQUESTDATA>
</IMPORTDATA>
</BODY>
</ENVELOPE>"""

    print("=" * 70)
    print(f"Updating Tally stock from Salesforce : {stock_name} ({part_no})")
    print(xml)
    print("=" * 70)

    response = _tally_post(xml)
    print(response.text)
    return response.text


# ==========================================
# Day Book / voucher number helpers
# ==========================================


def _export_requestdesc(report_name, extra_static_tags=""):

    static = _tally_static_variables(extra_static_tags)
    if not static:
        static = "<STATICVARIABLES>\n</STATICVARIABLES>"

    return f"""<REQUESTDESC>
<REPORTNAME>{report_name}</REPORTNAME>
{static}
</REQUESTDESC>"""


def _normalize_master_id(value):

    return re.sub(r"\D", "", str(value or "").strip())


def _parse_daybook_vouchers(response_text):

    try:
        root = ET.fromstring(_sanitize_tally_xml(response_text))
    except ET.ParseError:
        return []

    vouchers = []
    for voucher in root.findall(".//VOUCHER"):
        master_id = (
            voucher.findtext("MASTERID")
            or voucher.get("MASTERID")
            or ""
        )
        bill_names = []
        for bill in voucher.findall(".//BILLALLOCATIONS.LIST"):
            bill_name = _clean_tally_text(bill.findtext("NAME") or "")
            if bill_name:
                bill_names.append(bill_name)
        party = _clean_tally_text(
            voucher.findtext("PARTYLEDGERNAME")
            or voucher.findtext("PARTYNAME")
            or ""
        )
        number = _clean_tally_text(
            voucher.findtext("VOUCHERNUMBER")
            or voucher.findtext("VOUCHERNUM")
            or ""
        )
        # Skip empty collection placeholders (CMPINFO / wrapper nodes)
        if not number and not _normalize_master_id(master_id):
            continue

        vouchers.append({
            "number": number,
            "type": _clean_tally_text(voucher.findtext("VOUCHERTYPENAME") or ""),
            "narration": _clean_tally_text(voucher.findtext("NARRATION") or ""),
            "date": _clean_tally_text(voucher.findtext("DATE") or ""),
            "master_id": _normalize_master_id(master_id),
            "bill_names": bill_names,
            "party": party,
        })

    return vouchers


def _parse_last_vch_id(response_text):

    match = re.search(r"<LASTVCHID>\s*(\d+)\s*</LASTVCHID>", response_text or "")
    if match:
        return match.group(1)
    return None


def _fetch_voucher_by_master_id(vch_id, voucher_type=None):

    """Fetch one voucher by MASTERID via filtered collection.

    Object export by MasterId fails on this Tally build
    ("Could not find Voucher:<id>!"), so collection filter is used.
    """

    target_id = _normalize_master_id(vch_id)
    if not target_id:
        return None

    static = _tally_static_variables("<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>")
    xml = f"""<ENVELOPE>
<HEADER>
<VERSION>1</VERSION>
<TALLYREQUEST>Export</TALLYREQUEST>
<TYPE>Collection</TYPE>
<ID>VoucherByMasterId</ID>
</HEADER>
<BODY>
<DESC>
{static}
<TDL>
<TDLMESSAGE>
<COLLECTION NAME="VoucherByMasterId" ISMODIFY="No">
<TYPE>Voucher</TYPE>
<FETCH>VoucherNumber</FETCH>
<FETCH>VoucherTypeName</FETCH>
<FETCH>Narration</FETCH>
<FETCH>MasterId</FETCH>
<FETCH>Date</FETCH>
<FETCH>PartyLedgerName</FETCH>
<FILTER>MasterIdEq</FILTER>
</COLLECTION>
<SYSTEM TYPE="Formulae" NAME="MasterIdEq">$MasterId = {target_id}</SYSTEM>
</TDLMESSAGE>
</TDL>
</DESC>
</BODY>
</ENVELOPE>"""

    try:
        response = _tally_post(xml)
    except requests.RequestException:
        return None

    for voucher in _parse_daybook_vouchers(response.text):
        if voucher.get("master_id") != target_id:
            continue
        if not voucher.get("number"):
            continue
        return {
            "number": voucher.get("number"),
            "type": voucher.get("type") or voucher_type,
            "master_id": target_id,
        }

    return None


def get_voucher_from_import_response(
    voucher_response,
    voucher_type=None,
    max_attempts=8,
    retry_delay_seconds=0.5,
):

    """Read VOUCHERNUMBER for the voucher just created using Tally LASTVCHID."""

    import time

    vch_id = _parse_last_vch_id(voucher_response)
    if not vch_id:
        return None

    for attempt in range(max_attempts):
        if attempt:
            time.sleep(retry_delay_seconds)

        info = _fetch_voucher_by_master_id(vch_id, voucher_type=voucher_type)
        if info and info.get("number"):
            info["source"] = "lastvchid"
            info["master_id"] = vch_id
            return info

    return None


def _voucher_number_sort_key(voucher):

    number = str((voucher or {}).get("number") or "").strip()
    digits = re.sub(r"\D", "", number)
    return int(digits) if digits else 0


def _pick_latest_daybook_match(matches, voucher_type=None):

    if not matches:
        return None

    preferred_type = str(voucher_type or "").strip().lower()
    if preferred_type:
        typed_matches = [
            voucher
            for voucher in matches
            if (voucher.get("type") or "").strip().lower() == preferred_type
        ]
        if typed_matches:
            matches = typed_matches

    return max(matches, key=_voucher_number_sort_key)


def get_voucher_from_daybook(
    voucher_date,
    reference_id,
    voucher_type=None,
    reference_name=None,
    max_attempts=6,
    retry_delay_seconds=1.5,
):

    """Read VOUCHERNUMBER and VOUCHERTYPENAME from Tally Day Book."""

    import time

    date_str = str(voucher_date or "").strip()
    ref_id = str(reference_id or "").strip()

    if not date_str or not ref_id:
        return None

    extra_static = (
        "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>\n"
        f"<SVFROMDATE>{date_str}</SVFROMDATE>\n"
        f"<SVTODATE>{date_str}</SVTODATE>"
    )
    xml = f"""<ENVELOPE>
<HEADER>
<TALLYREQUEST>Export Data</TALLYREQUEST>
</HEADER>
<BODY>
<EXPORTDATA>
{_export_requestdesc("Day Book", extra_static)}
</EXPORTDATA>
</BODY>
</ENVELOPE>"""

    ref_key = ref_id.lower()
    name_keys = []
    if reference_name:
        normalized = str(reference_name).strip().lower()
        name_keys.extend({
            normalized,
            normalized.replace("-", " "),
            normalized.replace("-", ""),
        })

    for attempt in range(max_attempts):
        if attempt:
            time.sleep(retry_delay_seconds)
        else:
            time.sleep(0.5)

        response = _tally_post(xml)
        matches = []

        for voucher in _parse_daybook_vouchers(response.text):
            narration = (voucher.get("narration") or "").lower()
            voucher_number = voucher.get("number") or ""
            bill_blob = " ".join(voucher.get("bill_names") or []).lower()

            # Narration no longer carries SF Id — also match bill ref (e.g. DN-0093).
            matched_ref = ref_key in narration or ref_key in bill_blob
            matched_name = any(
                key and (key in narration or key in bill_blob)
                for key in name_keys
            )
            if not matched_ref and not matched_name:
                continue
            if not voucher_number:
                continue

            matches.append(voucher)

        if not matches:
            continue

        chosen = _pick_latest_daybook_match(matches, voucher_type=voucher_type)
        if not chosen:
            continue

        return {
            "number": chosen.get("number"),
            "type": chosen.get("type") or voucher_type,
            "source": "daybook",
        }

    return None


def get_voucher_by_master_id(
    voucher_date,
    master_id,
    voucher_type=None,
    max_attempts=6,
    retry_delay_seconds=1.5,
):

    """Read the voucher number by matching Day Book MASTERID against Tally LASTVCHID.

    This is the most reliable lookup: LASTVCHID from the create response equals
    the MASTERID of the exact voucher just created, so it does not depend on
    narration or reference text.
    """

    import time

    date_str = str(voucher_date or "").strip()
    target_id = str(master_id or "").strip()

    if not date_str or not target_id:
        return None

    extra_static = (
        "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>\n"
        f"<SVFROMDATE>{date_str}</SVFROMDATE>\n"
        f"<SVTODATE>{date_str}</SVTODATE>"
    )
    xml = f"""<ENVELOPE>
<HEADER>
<TALLYREQUEST>Export Data</TALLYREQUEST>
</HEADER>
<BODY>
<EXPORTDATA>
{_export_requestdesc("Day Book", extra_static)}
</EXPORTDATA>
</BODY>
</ENVELOPE>"""

    for attempt in range(max_attempts):
        if attempt:
            time.sleep(retry_delay_seconds)
        else:
            time.sleep(0.5)

        response = _tally_post(xml)

        want = _normalize_master_id(target_id)
        for voucher in _parse_daybook_vouchers(response.text):
            if voucher.get("master_id") != want:
                continue
            if not voucher.get("number"):
                continue
            return {
                "number": voucher.get("number"),
                "type": voucher.get("type") or voucher_type,
                "master_id": want,
                "source": "masterid",
            }

    return None


def get_voucher_after_create(
    voucher_response,
    voucher_date,
    reference_id,
    voucher_type=None,
    reference_name=None,
):

    # Prefer direct MASTERID collection (fast). Object-by-MasterId is unreliable here.
    info = get_voucher_from_import_response(
        voucher_response,
        voucher_type=voucher_type,
    )
    if info and info.get("number"):
        return info

    master_id = _parse_last_vch_id(voucher_response)
    if master_id:
        info = get_voucher_by_master_id(
            voucher_date,
            master_id,
            voucher_type=voucher_type,
        )
        if info and info.get("number"):
            return info

    # Fallback: Day Book match on bill ref (DN-xxxx) since narration is only "Created By".
    info = get_voucher_from_daybook(
        voucher_date,
        reference_id,
        voucher_type=voucher_type,
        reference_name=reference_name,
    )
    if info and info.get("number"):
        return info

    return None


# ==========================================
# Get Stock Items from Tally
# ==========================================

def get_tally_products():

    products = []
    for stock in get_tally_stock_catalog():
        products.append({
            "ProductCode": stock.get("part_no") or stock.get("name"),
            "PartNo": stock.get("part_no") or "",
            "Name": stock.get("name"),
            "Family": stock.get("parent"),
            "Unit_of_Measure__c": stock.get("unit"),
            "HSN_Code": stock.get("hsn"),
            "OpeningQuantity": stock.get("opening_qty"),
            "OpeningUnit": stock.get("opening_unit"),
            "RatePer": stock.get("rate_per"),
            "RateUnit": stock.get("rate_unit"),
            "RateSource": stock.get("rate_source"),
        })
    return products
