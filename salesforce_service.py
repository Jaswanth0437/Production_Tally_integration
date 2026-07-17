import requests
from config import (
    ACCOUNT_BILLING_CITY_FIELD,
    ACCOUNT_BILLING_COUNTRY_FIELD,
    ACCOUNT_BILLING_POSTAL_FIELD,
    ACCOUNT_BILLING_STATE_FIELD,
    ACCOUNT_BILLING_STREET_FIELD,
    ACCOUNT_GSTIN_FIELD,
    ACCOUNT_PAN_FIELD,
    CLIENT_ID,
    CLIENT_SECRET,
    DISPATCH_LINE_LOOKUP,
    DISPATCH_LINE_OBJECT,
    DISPATCH_LINE_QTY_FIELD,
    DISPATCH_OBJECT,
    DISPATCH_DESTINATION_FIELD,
    DISPATCH_FREIGHT_FIELD,
    DISPATCH_VOUCHER_TYPE_FIELD,
    DISPATCH_OPPORTUNITY_FIELD,
    DISPATCH_PARTY_FIELD,
    DISPATCH_SENT_TO_TALLY_FIELD,
    DISPATCH_TALLY_VOUCHER_NUMBER_FIELD,
    DISPATCH_VEHICLE_NO_FIELD,
    DISPATCH_VOUCHER_STATUS_FIELD,
    DISPATCH_WAREHOUSE_FIELD,
    OPPORTUNITY_ACCOUNT_FIELD,
    OPPORTUNITY_SHIPPING_CITY_FIELD,
    OPPORTUNITY_SHIPPING_COUNTRY_FIELD,
    OPPORTUNITY_SHIPPING_LOOKUP_FIELD,
    OPPORTUNITY_SHIPPING_POSTAL_FIELD,
    OPPORTUNITY_SHIPPING_STATE_FIELD,
    OPPORTUNITY_SHIPPING_STREET_FIELD,
    OPPORTUNITY_AMOUNT_FIELD,
    OPPORTUNITY_CLOSE_DATE_FIELD,
    OPPORTUNITY_OBJECT,
    OPPORTUNITY_PRODUCT_FAMILY_FIELD,
    OPPORTUNITY_SAME_AS_BILLING_FIELD,
    ORDER_MANAGE_BUNDLE_COUNT_FIELD,
    ORDER_MANAGE_FIXIT_QTY_FIELD,
    ORDER_MANAGE_FIXIT_RATE_FIELD,
    ORDER_MANAGE_FOAM_QTY_FIELD,
    ORDER_MANAGE_FOAM_RATE_FIELD,
    ORDER_MANAGE_LAMINATION_BRAND_FIELD,
    ORDER_MANAGE_LAMINATION_COLOR_FIELD,
    ORDER_MANAGE_LAMINATION_GRADE_FIELD,
    ORDER_MANAGE_LAMINATION_ITEM_CODE_FIELD,
    ORDER_MANAGE_LAMINATION_QTY_FIELD,
    ORDER_MANAGE_LAMINATION_RATE_FIELD,
    ORDER_MANAGE_LAMINATION_THICKNESS_FIELD,
    ORDER_MANAGE_LAMINATION_TOTAL_FIELD,
    ORDER_MANAGE_MTR_FIELD,
    ORDER_MANAGE_OBJECT,
    ORDER_MANAGE_OPPORTUNITY_FIELD,
    ORDER_MANAGE_PRO_CODE_FIELD,
    ORDER_MANAGE_REQUIRED_STOCK_FIELD,
    ORDER_MANAGE_ORDER_NAME_FIELD,
    ORDER_MANAGE_PRODUCT_INFO_FIELD,
    ORDER_MANAGE_PRODUCT_LOOKUP_FIELD,
    ORDER_MANAGE_PRODUCT_NAME_FIELD,
    PRODUCT_CODE_FIELD,
    PRODUCT_HSN_FIELD,
    PRODUCT_OBJECT,
    PRODUCT_FAMILY_FIELD,
    PRODUCT_INFO_FIELD,
    PRODUCT_LAMINATION_THICKNESS_FIELD,
    STOCK_PRODUCT_INFO_FIELD,
    STOCK_PRODUCT_LOOKUP_FIELD,
    STOCK_PRODUCT_NAME_FIELD,
    PASSWORD,
    REFRESH_TOKEN,
    SALESFORCE_AUTH_MODE,
    SALESFORCE_MY_DOMAIN_TOKEN_URL,
    TOKEN_URL,
    TRACK_DISPATCH_IN_SALESFORCE,
    TALLY_DEFAULT_HSN_CODE,
    USERNAME,
    WAREHOUSE_LOCATION_FIELD,
)


def _token_endpoint():

    if SALESFORCE_AUTH_MODE == "client_credentials":
        if not SALESFORCE_MY_DOMAIN_TOKEN_URL:
            raise ValueError(
                "Set SALESFORCE_MY_DOMAIN_TOKEN_URL in config.py for External Client App. "
                "Example: https://yourcompany--uatplus1.sandbox.my.salesforce.com/services/oauth2/token"
            )
        return SALESFORCE_MY_DOMAIN_TOKEN_URL

    return TOKEN_URL


def get_access_token():

    token_url = _token_endpoint()

    if SALESFORCE_AUTH_MODE == "client_credentials":
        payload = {
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        }
    elif SALESFORCE_AUTH_MODE == "refresh_token":
        if not REFRESH_TOKEN:
            raise ValueError("Set REFRESH_TOKEN in config.py")

        payload = {
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": REFRESH_TOKEN,
        }
    else:
        payload = {
            "grant_type": "password",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "username": USERNAME,
            "password": PASSWORD,
        }

    response = requests.post(
        token_url,
        data=payload
    )

    if not response.ok:
        try:
            error = response.json()
            message = error.get("error_description") or error.get("error")
        except ValueError:
            message = response.text

        raise ValueError(f"Salesforce login failed: {message}")

    return response.json()


def _run_query(query):

    token = get_access_token()

    access_token = token["access_token"]
    instance_url = token["instance_url"]

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    response = requests.get(
        f"{instance_url}/services/data/v64.0/query",
        headers=headers,
        params={"q": query}
    )

    if not response.ok:
        try:
            errors = response.json()
            if isinstance(errors, list) and errors:
                message = errors[0].get("message", response.text)
            else:
                message = response.text
        except ValueError:
            message = response.text

        raise ValueError(f"Salesforce query failed: {message}")

    return response.json()["records"]


def _update_record(object_name, record_id, payload):

    token = get_access_token()

    access_token = token["access_token"]
    instance_url = token["instance_url"]

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    response = requests.patch(
        f"{instance_url}/services/data/v64.0/sobjects/{object_name}/{record_id}",
        headers=headers,
        json=payload
    )

    return response.status_code == 204


# ==========================================
# Get Opportunity
# ==========================================

def get_opportunity(opportunity_id):

    query = f"""
    SELECT Id,
           Name,
           {OPPORTUNITY_AMOUNT_FIELD},
           {OPPORTUNITY_CLOSE_DATE_FIELD},
           Account__r.Name
    FROM {OPPORTUNITY_OBJECT}
    WHERE Id = '{opportunity_id}'
    """

    records = _run_query(query)

    if not records:
        return None

    return records[0]


def get_opportunity_amount(opportunity):

    return opportunity.get(OPPORTUNITY_AMOUNT_FIELD, 0)


def get_opportunity_account_name(opportunity):

    account = opportunity.get("Account__r") or {}

    return account.get("Name", "Unknown Customer")


def get_opportunity_close_date(opportunity):

    return opportunity.get(OPPORTUNITY_CLOSE_DATE_FIELD)


# ==========================================
# Update Opportunity
# ==========================================

def update_opportunity(opportunity_id):

    payload = {
        "Sent_To_Tally__c": True,
        "Voucher_Status__c": "Success"
    }

    return _update_record(OPPORTUNITY_OBJECT, opportunity_id, payload)

# ==========================================
# Get opportunity line items
# ==========================================

# Product_Family__c picklist values (UATplus1 Opportunity / Order Manage)
ORDER_MANAGE_PRODUCT_FAMILIES = (
    "Sarvesh PVC",
    "Cheran PVC",
    "Wall Panel PVC",
    "Wall Panel WPC",
    "UPVC",
    "Lamination Sheet",
    "WPC",
    "Foam Sheet",
    "Fixit",
)

ORDER_MANAGE_PRODUCT_FIELDS = {
    "UPVC": ("UPVC_Qty__c", "UPVC_Price_per_Qty__c", "UPVC_Price__c"),
    "Wall Panel PVC": ("Wall_Panel_Qty__c", "Wall_Panel_Price_per_Qty__c", None),
    "Wall Panel WPC": ("Wall_Panel_Qty__c", "Wall_Panel_Price_per_Qty__c", None),
    "Wall Panel": ("Wall_Panel_Qty__c", "Wall_Panel_Price_per_Qty__c", None),
    "WPC": ("WPC_Qty__c", "WPC_Price_Per_Qty__c", "WPC_Rate__c"),
    "Lamination Sheet": (
        ORDER_MANAGE_LAMINATION_QTY_FIELD,
        ORDER_MANAGE_LAMINATION_RATE_FIELD,
        ORDER_MANAGE_LAMINATION_TOTAL_FIELD,
    ),
    "Foam Sheet": (
        ORDER_MANAGE_FOAM_QTY_FIELD,
        ORDER_MANAGE_FOAM_RATE_FIELD,
        None,
    ),
    "Fixit": (
        ORDER_MANAGE_FIXIT_QTY_FIELD,
        ORDER_MANAGE_FIXIT_RATE_FIELD,
        None,
    ),
    "Sarvesh PVC": (ORDER_MANAGE_REQUIRED_STOCK_FIELD, None, None),
    "Cheran PVC": (ORDER_MANAGE_REQUIRED_STOCK_FIELD, None, None),
}

PVC_PANEL_FAMILIES = frozenset({"Sarvesh PVC", "Cheran PVC"})
WALL_PANEL_FAMILIES = frozenset({"Wall Panel PVC", "Wall Panel WPC"})
LAMINATION_PRODUCT_FAMILIES = frozenset({"Lamination Sheet"})
FOAM_SHEET_FAMILIES = frozenset({"Foam Sheet"})
FIXIT_FAMILIES = frozenset({"Fixit"})

PVC_PANEL_QTY_FALLBACK_FIELDS = (
    ORDER_MANAGE_REQUIRED_STOCK_FIELD,
    ORDER_MANAGE_BUNDLE_COUNT_FIELD,
    ORDER_MANAGE_MTR_FIELD,
)


def _is_lamination_family(family):

    family = str(family or "").strip()
    if family in LAMINATION_PRODUCT_FAMILIES:
        return True
    return "laminat" in family.lower()


def _is_wall_panel_family(family):

    family = str(family or "").strip()
    return family in WALL_PANEL_FAMILIES or family == "Wall Panel"


def _record_field(record, field_name):

    product = record.get("Product__r") or {}
    value = record.get(field_name)
    if value not in (None, ""):
        return value
    return product.get(field_name)


def _get_opportunity_family_by_id(opportunity_id):

    if not opportunity_id:
        return ""

    query = f"""
    SELECT {OPPORTUNITY_PRODUCT_FAMILY_FIELD}
    FROM {OPPORTUNITY_OBJECT}
    WHERE Id = '{opportunity_id}'
    """

    records = _run_query(query)
    if not records:
        return ""

    return str(records[0].get(OPPORTUNITY_PRODUCT_FAMILY_FIELD) or "").strip()


def _resolve_effective_product_family(record, opportunity_family=None):

    family = str(record.get("Product_Family__c") or "").strip()
    opportunity_family = str(opportunity_family or "").strip()

    if family in WALL_PANEL_FAMILIES:
        return family

    if family == "Wall Panel" or (
        _is_wall_panel_family(opportunity_family) and not family
    ):
        panel_type = str(_record_field(record, "Wall_Panel_Type__c") or "").strip().upper()
        if panel_type == "WPC":
            return "Wall Panel WPC"
        if panel_type == "PVC":
            return "Wall Panel PVC"
        if opportunity_family in WALL_PANEL_FAMILIES:
            return opportunity_family

    return family or opportunity_family or "Product"


def _is_pvc_panel_family(family):

    return str(family or "").strip() in PVC_PANEL_FAMILIES


def _resolve_product_family_fields(family):

    family = str(family or "").strip()
    fields = ORDER_MANAGE_PRODUCT_FIELDS.get(family)

    if fields:
        return fields

    if _is_wall_panel_family(family):
        return ORDER_MANAGE_PRODUCT_FIELDS["Wall Panel PVC"]

    if _is_lamination_family(family):
        return ORDER_MANAGE_PRODUCT_FIELDS["Lamination Sheet"]

    return (None, None, None)


def _first_positive_value(record, *field_names):

    for field_name in field_names:
        value = record.get(field_name)
        if value not in (None, "", 0):
            return value
    return None


def _lamination_display_name(record):

    parts = [
        record.get(ORDER_MANAGE_LAMINATION_BRAND_FIELD),
        record.get(ORDER_MANAGE_LAMINATION_COLOR_FIELD),
        record.get(ORDER_MANAGE_LAMINATION_GRADE_FIELD),
        record.get(ORDER_MANAGE_LAMINATION_THICKNESS_FIELD),
        record.get(ORDER_MANAGE_LAMINATION_ITEM_CODE_FIELD),
    ]
    parts = [str(part).strip() for part in parts if part not in (None, "")]
    if parts:
        return " - ".join(parts)
    return ""


def _foam_sheet_display_name(record):

    parts = [
        record.get("Foam_Sheet_Brand__c"),
        record.get("Foam_Sheet_Design_Name__c"),
        record.get("Foam_Sheet_Color__c"),
        record.get("Foam_Sheet_Thickness__c"),
        record.get("Foam_Sheet_Density__c"),
    ]
    parts = [str(part).strip() for part in parts if part not in (None, "")]
    if parts:
        return " - ".join(parts)
    return ""


def _fixit_display_name(record):

    parts = [
        record.get("Fixit_Brand__c"),
        record.get("Fixit_Design_Name__c"),
        record.get("Fixit_Design_Code__c"),
        record.get("Fixit_Design_Type__c"),
    ]
    parts = [str(part).strip() for part in parts if part not in (None, "")]
    if parts:
        return " - ".join(parts)
    return ""


def _wall_panel_display_name(record, family):

    if family == "Wall Panel WPC":
        parts = [
            _record_field(record, "Wall_Panel_WPC_Type__c"),
            _record_field(record, "Wall_Panel_WPC_Design__c"),
            _record_field(record, "Wall_Panel_Brand__c"),
        ]
    else:
        parts = [
            _record_field(record, "Wall_Panel_PVC_Design__c"),
            _record_field(record, "Wall_Panel_PVC_Color__c"),
            _record_field(record, "Wall_Panel_Brand__c"),
            _record_field(record, "Wall_Panel_Type__c"),
        ]
    parts = [str(part).strip() for part in parts if part not in (None, "")]
    if parts:
        return " - ".join(parts)
    return ""


def _order_manage_product_name(record, family, product):

    if _is_lamination_family(family):
        return (
            record.get(ORDER_MANAGE_ORDER_NAME_FIELD)
            or record.get(ORDER_MANAGE_PRODUCT_INFO_FIELD)
            or _lamination_display_name(record)
            or product.get("Name")
            or family
        )

    if family in FOAM_SHEET_FAMILIES:
        return (
            record.get(ORDER_MANAGE_ORDER_NAME_FIELD)
            or _foam_sheet_display_name(record)
            or product.get("Name")
            or family
        )

    if family in FIXIT_FAMILIES:
        return (
            record.get(ORDER_MANAGE_ORDER_NAME_FIELD)
            or _fixit_display_name(record)
            or product.get("Name")
            or family
        )

    if _is_wall_panel_family(family):
        return (
            record.get(ORDER_MANAGE_ORDER_NAME_FIELD)
            or _wall_panel_display_name(record, family)
            or product.get("Name")
            or family
        )

    return (
        record.get(ORDER_MANAGE_PRODUCT_NAME_FIELD)
        or record.get(ORDER_MANAGE_ORDER_NAME_FIELD)
        or product.get("Name")
        or family
    )


def _order_manage_product_info(record, family):

    # Invoice description must be the raw Salesforce Product_Info__c value
    # for every product family. Do not synthesize it from family-specific
    # fields such as groove, colour, brand, grade, or thickness.
    return str(record.get(ORDER_MANAGE_PRODUCT_INFO_FIELD) or "").strip()


def _order_manage_query_fields():

    return f"""
           Product_Family__c,
           Product_Code__c,
           {ORDER_MANAGE_PRO_CODE_FIELD},
           {ORDER_MANAGE_REQUIRED_STOCK_FIELD},
           {ORDER_MANAGE_BUNDLE_COUNT_FIELD},
           {ORDER_MANAGE_MTR_FIELD},
           Product__r.Name,
           Product__r.{PRODUCT_CODE_FIELD},
           Product__r.{PRODUCT_HSN_FIELD},
           Product__r.Product_Family__c,
           Product__r.Base_Price__c,
           Product__r.Wall_Panel_Brand__c,
           Product__r.Wall_Panel_Type__c,
           Product__r.Wall_Panel_PVC_Design__c,
           Product__r.Wall_Panel_PVC_Color__c,
           Product__r.Wall_Panel_WPC_Type__c,
           Product__r.Wall_Panel_WPC_Design__c,
           {ORDER_MANAGE_LAMINATION_ITEM_CODE_FIELD},
           {ORDER_MANAGE_LAMINATION_BRAND_FIELD},
           {ORDER_MANAGE_LAMINATION_COLOR_FIELD},
           {ORDER_MANAGE_LAMINATION_GRADE_FIELD},
           {ORDER_MANAGE_LAMINATION_THICKNESS_FIELD},
           {ORDER_MANAGE_LAMINATION_QTY_FIELD},
           {ORDER_MANAGE_LAMINATION_RATE_FIELD},
           {ORDER_MANAGE_LAMINATION_TOTAL_FIELD},
           {ORDER_MANAGE_FOAM_QTY_FIELD},
           {ORDER_MANAGE_FOAM_RATE_FIELD},
           Foam_Sheet_Brand__c,
           Foam_Sheet_Design_Name__c,
           Foam_Sheet_Color__c,
           Foam_Sheet_Thickness__c,
           Foam_Sheet_Density__c,
           {ORDER_MANAGE_FIXIT_QTY_FIELD},
           {ORDER_MANAGE_FIXIT_RATE_FIELD},
           Fixit_Brand__c,
           Fixit_Design_Name__c,
           Fixit_Design_Code__c,
           Fixit_Design_Type__c,
           Wall_Panel_Qty__c,
           Wall_Panel_Price_per_Qty__c,
           Wall_Panel_Brand__c,
           Wall_Panel_Type__c,
           Wall_Panel_PVC_Design__c,
           Wall_Panel_PVC_Color__c,
           Wall_Panel_WPC_Type__c,
           Wall_Panel_WPC_Design__c,
           UPVC_Qty__c,
           UPVC_Price_per_Qty__c,
           UPVC_Price__c,
           WPC_Qty__c,
           WPC_Price_Per_Qty__c,
           WPC_Rate__c"""


def _get_order_manage_context(opportunity_id):

    if not opportunity_id:
        return {"by_code": {}, "by_family": {}, "all_lines": []}

    opportunity_family = _get_opportunity_family_by_id(opportunity_id)

    query = f"""
    SELECT Id,
           {ORDER_MANAGE_PRODUCT_LOOKUP_FIELD},
           {ORDER_MANAGE_PRODUCT_NAME_FIELD},
           {ORDER_MANAGE_ORDER_NAME_FIELD},
           {ORDER_MANAGE_PRODUCT_INFO_FIELD},
           {_order_manage_query_fields().strip()}
    FROM {ORDER_MANAGE_OBJECT}
    WHERE {ORDER_MANAGE_OPPORTUNITY_FIELD} = '{opportunity_id}'
    """

    try:
        records = _run_query(query)
    except ValueError:
        return {"by_code": {}, "by_family": {}, "all_lines": []}

    by_code = {}
    by_family = {}
    all_lines = []

    for record in records:
        line = _normalize_order_manage_line(
            record, opportunity_family=opportunity_family
        )

        if not line:
            continue

        all_lines.append(line)

        for code_value in (
            line.get("code"),
            record.get(ORDER_MANAGE_LAMINATION_ITEM_CODE_FIELD),
            record.get(ORDER_MANAGE_PRO_CODE_FIELD),
            record.get("Product_Code__c"),
        ):
            code = str(code_value or "").strip().upper()
            if code:
                by_code[code] = line

        family_key = str(line.get("name") or "").strip().upper()
        if family_key:
            by_family.setdefault(family_key, []).append(line)

    return {"by_code": by_code, "by_family": by_family, "all_lines": all_lines}


def _get_order_manage_by_product_code(opportunity_id):

    return _get_order_manage_context(opportunity_id)["by_code"]


def _match_order_manage_line(line, order_manage_ctx, opportunity_family=None):

    by_code = order_manage_ctx.get("by_code") or {}
    by_family = order_manage_ctx.get("by_family") or {}
    all_lines = order_manage_ctx.get("all_lines") or []

    code = str(line.get("code") or "").strip().upper()
    if code:
        order_line = by_code.get(code)
        if order_line:
            return order_line

    families_to_try = []
    for family_value in (line.get("name"), opportunity_family):
        family = str(family_value or "").strip().upper()
        if family and family not in families_to_try:
            families_to_try.append(family)

    for family in families_to_try:
        candidates = by_family.get(family, [])
        if len(candidates) == 1:
            return candidates[0]

    if _is_lamination_family(line.get("name")) or _is_lamination_family(
        opportunity_family
    ):
        lamination_lines = [
            order_line
            for order_line in all_lines
            if _is_lamination_family(order_line.get("name"))
        ]
        if len(lamination_lines) == 1:
            return lamination_lines[0]

    if _is_wall_panel_family(line.get("name")) or _is_wall_panel_family(
        opportunity_family
    ):
        wall_panel_lines = [
            order_line
            for order_line in all_lines
            if _is_wall_panel_family(order_line.get("name"))
            and (
                not opportunity_family
                or not _is_wall_panel_family(opportunity_family)
                or order_line.get("name") == opportunity_family
            )
        ]
        if len(wall_panel_lines) == 1:
            return wall_panel_lines[0]

    if len(all_lines) == 1:
        return all_lines[0]

    return None


def _enrich_dispatch_line_from_order_manage(
    line, order_manage_ctx, opportunity_family=None
):

    if isinstance(order_manage_ctx, dict) and "by_code" in order_manage_ctx:
        ctx = order_manage_ctx
    else:
        ctx = {"by_code": order_manage_ctx or {}, "by_family": {}, "all_lines": []}

    order_line = _match_order_manage_line(line, ctx, opportunity_family)

    if not order_line:
        return line

    quantity = float(order_line.get("quantity") or 0)
    unit_price = float(order_line.get("unit_price") or 0)

    if not quantity:
        quantity = float(line.get("quantity") or 0)

    if not unit_price:
        unit_price = float(line.get("unit_price") or 0)

    if not quantity and not unit_price:
        return line

    if quantity and unit_price:
        total_price = round(quantity * unit_price, 2)
    else:
        total_price = float(line.get("total_price") or 0)

    enriched = {
        **line,
        "name": order_line.get("name") or line.get("name"),
        "quantity": quantity or line.get("quantity"),
        "unit_price": unit_price or line.get("unit_price"),
        "total_price": total_price,
    }

    if order_line.get("code"):
        enriched["code"] = order_line["code"]
    if order_line.get("description"):
        enriched["description"] = order_line["description"]
    if order_line.get("product_info"):
        enriched["product_info"] = order_line["product_info"]
    if order_line.get("product_name"):
        enriched["product_name"] = order_line["product_name"]
    if order_line.get("hsn"):
        enriched["hsn"] = order_line["hsn"]

    return enriched


def _format_stock_description(product_info, product_name):

    product_info = str(product_info or "").strip()
    return product_info


def _resolve_product_hsn(product=None):

    product = product or {}
    hsn = str(product.get(PRODUCT_HSN_FIELD) or "").strip()
    if hsn:
        return hsn
    return TALLY_DEFAULT_HSN_CODE


def _normalize_order_manage_line(record, opportunity_family=None):

    product = record.get("Product__r") or {}
    family = _resolve_effective_product_family(record, opportunity_family)
    code = (
        record.get("Product_Code__c")
        or record.get(ORDER_MANAGE_PRO_CODE_FIELD)
        or record.get(ORDER_MANAGE_LAMINATION_ITEM_CODE_FIELD)
        or product.get(PRODUCT_CODE_FIELD)
        or ""
    )

    product_name = _order_manage_product_name(record, family, product)
    product_info = _order_manage_product_info(record, family)
    description = _format_stock_description(product_info, product_name)
    hsn = _resolve_product_hsn(product)

    qty_field, rate_field, total_field = _resolve_product_family_fields(family)

    quantity = record.get(qty_field) if qty_field else None

    if not quantity and _is_pvc_panel_family(family):
        quantity = _first_positive_value(record, *PVC_PANEL_QTY_FALLBACK_FIELDS)

    unit_price = record.get(rate_field) if rate_field else 0
    total_field_value = record.get(total_field) if total_field else None

    if family == "WPC" and not unit_price:
        unit_price = record.get("WPC_Rate__c") or record.get("WPC_Price_Per_Qty__c") or 0

    if _is_lamination_family(family) and not unit_price:
        unit_price = record.get(ORDER_MANAGE_LAMINATION_RATE_FIELD) or 0

    if family == "UPVC" and not unit_price:
        unit_price = product.get("Base_Price__c") or 0

    total_price = total_field_value

    if not quantity:
        return None

    if not unit_price and total_price and quantity:
        unit_price = float(total_price) / float(quantity)

    if total_price in (None, 0) and quantity and unit_price:
        total_price = float(quantity) * float(unit_price)
    else:
        total_price = total_price or 0

    return {
        "name": family,
        "product_name": product_name,
        "product_info": product_info,
        "code": code,
        "description": description,
        "hsn": hsn,
        "quantity": quantity,
        "unit_price": unit_price or 0,
        "total_price": total_price,
    }


def get_opportunity_products(opportunity_id):

    opportunity_family = _get_opportunity_family_by_id(opportunity_id)

    query = f"""
    SELECT Id,
           {_order_manage_query_fields()},
           {ORDER_MANAGE_PRODUCT_LOOKUP_FIELD},
           {ORDER_MANAGE_PRODUCT_NAME_FIELD},
           {ORDER_MANAGE_ORDER_NAME_FIELD},
           {ORDER_MANAGE_PRODUCT_INFO_FIELD}
    FROM {ORDER_MANAGE_OBJECT}
    WHERE {ORDER_MANAGE_OPPORTUNITY_FIELD} = '{opportunity_id}'
    """

    try:
        records = _run_query(query)
    except ValueError:
        return []

    line_items = []

    for record in records:
        line = _normalize_order_manage_line(
            record, opportunity_family=opportunity_family
        )

        if line:
            line_items.append(line)

    return line_items


def _get_opportunity_product_family(dispatch):

    opportunity = (dispatch or {}).get("Opportunity__r") or {}
    return str(opportunity.get(OPPORTUNITY_PRODUCT_FAMILY_FIELD) or "").strip()


def _normalize_dispatch_line(record, product_family=None):

    stock = record.get("Stock__r") or {}
    product = stock.get("Product__r") or {}
    family = str(product_family or "").strip()

    product_name = (
        stock.get(STOCK_PRODUCT_NAME_FIELD)
        or product.get("Name")
        or stock.get("Name")
        or family
        or record.get("Name")
        or "Unknown Product"
    )
    name = family or product_name
    code = (
        record.get("Product_Code__c")
        or stock.get(PRODUCT_CODE_FIELD)
        or product.get(PRODUCT_CODE_FIELD)
        or ""
    )
    product_info = stock.get(STOCK_PRODUCT_INFO_FIELD) or ""
    description = _format_stock_description(product_info, product_name)
    hsn = _resolve_product_hsn(product)

    quantity = (
        record.get(DISPATCH_LINE_QTY_FIELD)
        or record.get("Quantity__c")
        or 0
    )

    unit_price = record.get("Unit_Price__c") or 0
    total_price = record.get("Total_Price__c")

    if total_price in (None, 0) and quantity and unit_price:
        total_price = float(quantity) * float(unit_price)
    else:
        total_price = total_price or 0

    return {
        "name": name,
        "product_name": product_name,
        "product_info": product_info,
        "code": code,
        "description": description,
        "hsn": hsn,
        "quantity": quantity,
        "unit_price": unit_price,
        "total_price": total_price,
    }


def _normalize_opportunity_line_for_dispatch(product):

    if "PricebookEntry" in product:
        product_info = product["PricebookEntry"]["Product2"]
        return {
            "name": product_info["Name"],
            "code": product_info.get("ProductCode", ""),
            "quantity": product["Quantity"],
            "unit_price": product["UnitPrice"],
            "total_price": product["TotalPrice"],
        }

    return product


def _header_fallback_line(dispatch):

    quantity = (
        dispatch.get("No_of_Scanned_Quantity__c")
        or dispatch.get("Bundles__c")
        or 1
    )

    opportunity = dispatch.get("Opportunity__r") or {}
    amount = opportunity.get(OPPORTUNITY_AMOUNT_FIELD) or 0
    unit_price = amount / quantity if quantity else amount

    product_family = _get_opportunity_product_family(dispatch)

    return [{
        "name": product_family or "Dispatch Item",
        "product_name": product_family or "Dispatch Item",
        "code": dispatch.get("Product_Code__c") or "",
        "description": "",
        "hsn": TALLY_DEFAULT_HSN_CODE,
        "quantity": quantity,
        "unit_price": unit_price,
        "total_price": amount,
    }]


def get_dispatch_party_name(dispatch):

    party_name = dispatch.get(DISPATCH_PARTY_FIELD)

    if party_name:
        return party_name

    opportunity = dispatch.get("Opportunity__r") or {}
    account = opportunity.get("Account__r") or {}

    if account.get("Name"):
        return account["Name"]

    return opportunity.get("Name", "Unknown Customer")


def get_dispatch_voucher_type(dispatch):

    return str((dispatch or {}).get(DISPATCH_VOUCHER_TYPE_FIELD) or "").strip()


def _dispatch_select_fields():

    tally_fields = ""
    if TRACK_DISPATCH_IN_SALESFORCE:
        tally_fields = f""",
           {DISPATCH_SENT_TO_TALLY_FIELD},
           {DISPATCH_VOUCHER_STATUS_FIELD},
           {DISPATCH_TALLY_VOUCHER_NUMBER_FIELD}"""

    return f"""
           Id,
           Name,
           Dispatched_Date__c,
           Planned_Date__c,
           {DISPATCH_PARTY_FIELD},
           Product_Code__c,
           {DISPATCH_OPPORTUNITY_FIELD},
           Opportunity__r.Name,
           Opportunity__r.{OPPORTUNITY_PRODUCT_FAMILY_FIELD},
           Opportunity__r.{OPPORTUNITY_AMOUNT_FIELD},
           Opportunity__r.{OPPORTUNITY_SHIPPING_LOOKUP_FIELD},
           Opportunity__r.{OPPORTUNITY_SAME_AS_BILLING_FIELD},
           Opportunity__r.Shipping_Address__r.Name,
           Opportunity__r.Shipping_Address__r.{ACCOUNT_BILLING_STREET_FIELD},
           Opportunity__r.Shipping_Address__r.{ACCOUNT_BILLING_CITY_FIELD},
           Opportunity__r.Shipping_Address__r.{ACCOUNT_BILLING_STATE_FIELD},
           Opportunity__r.Shipping_Address__r.{ACCOUNT_BILLING_COUNTRY_FIELD},
           Opportunity__r.Shipping_Address__r.{ACCOUNT_BILLING_POSTAL_FIELD},
           Opportunity__r.Shipping_Address__r.{ACCOUNT_GSTIN_FIELD},
           Opportunity__r.{OPPORTUNITY_SHIPPING_STREET_FIELD},
           Opportunity__r.{OPPORTUNITY_SHIPPING_CITY_FIELD},
           Opportunity__r.{OPPORTUNITY_SHIPPING_STATE_FIELD},
           Opportunity__r.{OPPORTUNITY_SHIPPING_COUNTRY_FIELD},
           Opportunity__r.{OPPORTUNITY_SHIPPING_POSTAL_FIELD},
           Opportunity__r.Account__r.Name,
           Opportunity__r.Account__r.{ACCOUNT_BILLING_STREET_FIELD},
           Opportunity__r.Account__r.{ACCOUNT_BILLING_CITY_FIELD},
           Opportunity__r.Account__r.{ACCOUNT_BILLING_STATE_FIELD},
           Opportunity__r.Account__r.{ACCOUNT_BILLING_COUNTRY_FIELD},
           Opportunity__r.Account__r.{ACCOUNT_BILLING_POSTAL_FIELD},
           Opportunity__r.Account__r.{ACCOUNT_GSTIN_FIELD},
           Opportunity__r.Account__r.{ACCOUNT_PAN_FIELD},
           Opportunity__r.Account__r.BillingStreet,
           Opportunity__r.Account__r.BillingCity,
           Opportunity__r.Account__r.BillingState,
           Opportunity__r.Account__r.BillingCountry,
           Opportunity__r.Account__r.BillingPostalCode,
           No_of_Scanned_Quantity__c,
           Bundles__c,
           Status__c,
           {DISPATCH_VEHICLE_NO_FIELD},
           {DISPATCH_DESTINATION_FIELD},
           {DISPATCH_FREIGHT_FIELD},
           {DISPATCH_VOUCHER_TYPE_FIELD},
           {DISPATCH_WAREHOUSE_FIELD},
           Warehouses__r.Name,
           Warehouses__r.{WAREHOUSE_LOCATION_FIELD}
           {tally_fields}
    """


def get_dispatch(dispatch_id):

    select_fields = _dispatch_select_fields()

    query_by_id = f"""
    SELECT {select_fields}
    FROM {DISPATCH_OBJECT}
    WHERE Id = '{dispatch_id}'
    """

    records = _run_query(query_by_id)

    if records:
        return records[0]

    query_by_opportunity = f"""
    SELECT {select_fields}
    FROM {DISPATCH_OBJECT}
    WHERE {DISPATCH_OPPORTUNITY_FIELD} = '{dispatch_id}'
    ORDER BY CreatedDate DESC
    LIMIT 1
    """

    records = _run_query(query_by_opportunity)

    if records:
        return records[0]

    return None


def get_dispatch_line_items(dispatch_id, dispatch=None):

    if dispatch is None:
        dispatch = get_dispatch(dispatch_id)

    product_family = _get_opportunity_product_family(dispatch)

    query = f"""
    SELECT Id,
           Name,
           Product_Code__c,
           {DISPATCH_LINE_QTY_FIELD},
           Stock__r.Name,
           Stock__r.{STOCK_PRODUCT_NAME_FIELD},
           Stock__r.{STOCK_PRODUCT_INFO_FIELD},
           Stock__r.{PRODUCT_CODE_FIELD},
           Stock__r.{STOCK_PRODUCT_LOOKUP_FIELD},
           Stock__r.Product__r.Name,
           Stock__r.Product__r.{PRODUCT_CODE_FIELD},
           Stock__r.Product__r.{PRODUCT_HSN_FIELD}
    FROM {DISPATCH_LINE_OBJECT}
    WHERE {DISPATCH_LINE_LOOKUP} = '{dispatch_id}'
    """

    try:
        records = _run_query(query)
    except requests.HTTPError:
        records = []

    line_items = [
        _normalize_dispatch_line(record, product_family=product_family)
        for record in records
    ]

    if line_items:
        opportunity_id = (dispatch or {}).get(DISPATCH_OPPORTUNITY_FIELD)

        if opportunity_id:
            order_manage_ctx = _get_order_manage_context(opportunity_id)
            line_items = [
                _enrich_dispatch_line_from_order_manage(
                    line,
                    order_manage_ctx,
                    opportunity_family=product_family,
                )
                for line in line_items
            ]

        return line_items

    opportunity_id = (dispatch or {}).get(DISPATCH_OPPORTUNITY_FIELD)

    if opportunity_id:
        opportunity_products = get_opportunity_products(opportunity_id)

        if opportunity_products:
            return opportunity_products

    if dispatch:
        return _header_fallback_line(dispatch)

    return []


def update_dispatch(dispatch_id, voucher_number=None, voucher_type=None):

    if not TRACK_DISPATCH_IN_SALESFORCE:
        return True

    payload = {
        DISPATCH_SENT_TO_TALLY_FIELD: True,
        DISPATCH_VOUCHER_STATUS_FIELD: "Success",
    }

    if voucher_number:
        payload[DISPATCH_TALLY_VOUCHER_NUMBER_FIELD] = str(voucher_number)

    return _update_record(DISPATCH_OBJECT, dispatch_id, payload)


def _first_nonempty(*values):

    for value in values:
        if value not in (None, ""):
            return value
    return None


def _address_from_account(account):

    return {
        "name": account.get("Name"),
        "street": _first_nonempty(
            account.get(ACCOUNT_BILLING_STREET_FIELD),
            account.get("BillingStreet"),
        ),
        "city": _first_nonempty(
            account.get(ACCOUNT_BILLING_CITY_FIELD),
            account.get("BillingCity"),
        ),
        "state": _first_nonempty(
            account.get(ACCOUNT_BILLING_STATE_FIELD),
            account.get("BillingState"),
        ),
        "country": _first_nonempty(
            account.get(ACCOUNT_BILLING_COUNTRY_FIELD),
            account.get("BillingCountry"),
            "India",
        ),
        "postal": _first_nonempty(
            account.get(ACCOUNT_BILLING_POSTAL_FIELD),
            account.get("BillingPostalCode"),
        ),
        "gstin": account.get(ACCOUNT_GSTIN_FIELD),
        "pan": account.get(ACCOUNT_PAN_FIELD),
    }


def get_dispatch_bill_to(dispatch):

    account = ((dispatch.get("Opportunity__r") or {}).get("Account__r") or {})
    return _address_from_account(account)


def _get_dispatch_consignee(dispatch):

    opp = dispatch.get("Opportunity__r") or {}
    account = opp.get("Account__r") or {}

    if opp.get(OPPORTUNITY_SAME_AS_BILLING_FIELD):
        return _address_from_account(account)

    shipping_account = opp.get("Shipping_Address__r") or {}
    return {
        "name": shipping_account.get("Name"),
        "street": (
            opp.get(OPPORTUNITY_SHIPPING_STREET_FIELD)
            or shipping_account.get(ACCOUNT_BILLING_STREET_FIELD)
        ),
        "city": (
            opp.get(OPPORTUNITY_SHIPPING_CITY_FIELD)
            or shipping_account.get(ACCOUNT_BILLING_CITY_FIELD)
        ),
        "state": (
            opp.get(OPPORTUNITY_SHIPPING_STATE_FIELD)
            or shipping_account.get(ACCOUNT_BILLING_STATE_FIELD)
        ),
        "country": (
            opp.get(OPPORTUNITY_SHIPPING_COUNTRY_FIELD)
            or shipping_account.get(ACCOUNT_BILLING_COUNTRY_FIELD)
        ),
        "postal": (
            opp.get(OPPORTUNITY_SHIPPING_POSTAL_FIELD)
            or shipping_account.get(ACCOUNT_BILLING_POSTAL_FIELD)
        ),
        "gstin": shipping_account.get(ACCOUNT_GSTIN_FIELD),
    }


def get_dispatch_invoice_meta(dispatch):

    consignee = _get_dispatch_consignee(dispatch)
    warehouse = dispatch.get("Warehouses__r") or {}

    return {
        "consignee": consignee.get("name"),
        "shipping_street": consignee.get("street"),
        "shipping_city": consignee.get("city"),
        "shipping_state": consignee.get("state"),
        "shipping_country": consignee.get("country"),
        "shipping_postal": consignee.get("postal"),
        "shipping_gstin": consignee.get("gstin"),
        "dispatch_from_name": warehouse.get("Name"),
        "dispatch_from_location": warehouse.get(WAREHOUSE_LOCATION_FIELD),
        "vehicle_number": dispatch.get(DISPATCH_VEHICLE_NO_FIELD),
        "destination": dispatch.get(DISPATCH_DESTINATION_FIELD),
        "freight": dispatch.get(DISPATCH_FREIGHT_FIELD),
    }


def get_products_by_ids(product_ids):

    """Fetch Product__c rows by Id (one or many)."""

    ids = []
    seen = set()
    for raw in product_ids or []:
        pid = str(raw or "").strip()
        if pid and pid not in seen:
            seen.add(pid)
            ids.append(pid)

    if not ids:
        return []

    escaped = []
    for pid in ids:
        escaped.append("'" + pid.replace("\\", "\\\\").replace("'", "\\'") + "'")

    query = f"""
    SELECT Id,
           Name,
           {PRODUCT_CODE_FIELD},
           {PRODUCT_FAMILY_FIELD},
           {PRODUCT_INFO_FIELD},
           {PRODUCT_HSN_FIELD},
           {PRODUCT_LAMINATION_THICKNESS_FIELD}
    FROM {PRODUCT_OBJECT}
    WHERE Id IN ({', '.join(escaped)})
    """
    rows = _run_query(query)
    products = []
    for row in rows:
        products.append({
            "id": row.get("Id"),
            "name": str(row.get("Name") or "").strip(),
            "code": str(row.get(PRODUCT_CODE_FIELD) or "").strip(),
            "family": str(row.get(PRODUCT_FAMILY_FIELD) or "").strip(),
            "product_info": str(row.get(PRODUCT_INFO_FIELD) or "").strip(),
            "hsn": str(row.get(PRODUCT_HSN_FIELD) or "").strip(),
            "thickness": str(row.get(PRODUCT_LAMINATION_THICKNESS_FIELD) or "").strip(),
        })
    return products

