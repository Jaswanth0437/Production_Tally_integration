import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env for local dev. On Render, set the same variable names in Environment.
load_dotenv(Path(__file__).resolve().parent / ".env")

def _required_env(name):

    value = os.getenv(name)
    if value is None or not str(value).strip():
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Set it in .env (local) or Render Environment."
        )
    return str(value).strip()


# =============================================================================
# SENSITIVE / DEPLOYMENT — env only (no defaults in code)
# =============================================================================

CLIENT_ID = _required_env("SALESFORCE_CLIENT_ID")
CLIENT_SECRET = _required_env("SALESFORCE_CLIENT_SECRET")
SALESFORCE_MY_DOMAIN_TOKEN_URL = _required_env("SALESFORCE_MY_DOMAIN_TOKEN_URL")

TALLY_URL = _required_env("TALLY_URL")
TALLY_COMPANY_NAME = _required_env("TALLY_COMPANY_NAME")
TALLY_USERNAME = _required_env("TALLY_USERNAME")
TALLY_PASSWORD = _required_env("TALLY_PASSWORD")

# Optional secrets — only needed for password / refresh_token auth modes
USERNAME = os.getenv("SALESFORCE_USERNAME", "").strip()
PASSWORD = os.getenv("SALESFORCE_PASSWORD", "").strip()
REFRESH_TOKEN = os.getenv("SALESFORCE_REFRESH_TOKEN", "").strip()


# =============================================================================
# APP SETTINGS — config.py only (not in .env)
# =============================================================================

SALESFORCE_AUTH_MODE = "client_credentials"
TOKEN_URL = "https://login.salesforce.com/services/oauth2/token"

TALLY_VOUCHER_MODE = "item_invoice"
TALLY_GODOWN_NAME = "Main Location"
TALLY_BATCH_NAME = "Primary Batch"

# Salesforce Warehouse Name (Warehouse N …) → existing Tally godown.
# Matching is by warehouse number in the name (e.g. "Warehouse 2 - Laminates…").
TALLY_WAREHOUSE_GODOWN_MAP = {
    "1": "A Main Location",
    "2": "Paniampalli",
    "3": "Chennai Branch",
}
# Reverse: Tally godown name → Salesforce warehouse number ("1"/"2"/"3")
TALLY_GODOWN_TO_WAREHOUSE_NO = {
    godown: number for number, godown in TALLY_WAREHOUSE_GODOWN_MAP.items()
}

TRACK_DISPATCH_IN_SALESFORCE = True

TALLY_DEFAULT_HSN_CODE = "32642000"
TALLY_UNIT_NAME = "Nos"

TALLY_ENABLE_GST = True
TALLY_GST_INVOICE_VIEW = True
TALLY_COMPANY_STATE = "Tamil Nadu"
TALLY_COMPANY_GSTIN = "33CEZME1524M2KG"
TALLY_CGST_RATE = 9.0
TALLY_SGST_RATE = 9.0
TALLY_IGST_RATE = 18.0
TALLY_CGST_LEDGER = "CGST"
TALLY_SGST_LEDGER = "SGST"
TALLY_IGST_LEDGER = "IGST"

TALLY_ENABLE_ROUND_OFF = True
TALLY_ROUND_OFF_LEDGER = "Round Off"
TALLY_ROUND_OFF_PARENT = "Indirect Expenses"
TALLY_ROUNDING_METHOD = "Normal Rounding"
TALLY_ROUNDING_LIMIT = 1


# =============================================================================
# SALESFORCE FIELD MAP — config.py only (Production org)
# =============================================================================

ACCOUNT_BILLING_STREET_FIELD = "Street__c"
ACCOUNT_BILLING_CITY_FIELD = "City__c"
ACCOUNT_BILLING_STATE_FIELD = "State__c"
ACCOUNT_BILLING_COUNTRY_FIELD = "Country__c"
ACCOUNT_BILLING_POSTAL_FIELD = "Postal_Code__c"
ACCOUNT_GSTIN_FIELD = "GST__c"
ACCOUNT_PAN_FIELD = "PAN__c"

OPPORTUNITY_SHIPPING_LOOKUP_FIELD = "Shipping_Address__c"
OPPORTUNITY_SAME_AS_BILLING_FIELD = "Same_as_Billing_Address__c"
OPPORTUNITY_SHIPPING_STREET_FIELD = "Shipping_Street__c"
OPPORTUNITY_SHIPPING_CITY_FIELD = "Shipping_City__c"
OPPORTUNITY_SHIPPING_STATE_FIELD = "Shipping_State__c"
OPPORTUNITY_SHIPPING_COUNTRY_FIELD = "Shipping_Country__c"
OPPORTUNITY_SHIPPING_POSTAL_FIELD = "Shipping_Postal_Code__c"

DISPATCH_OBJECT = "Dispatch__c"
DISPATCH_LINE_OBJECT = "Dispatch_Line_Item__c"
DISPATCH_LINE_LOOKUP = "Dispatch__c"
DISPATCH_LINE_QTY_FIELD = "Total_Bundles__c"
DISPATCH_DATE_FIELD = "Dispatched_Date__c"
DISPATCH_PARTY_FIELD = "Party_Name__c"
DISPATCH_OPPORTUNITY_FIELD = "Opportunity__c"

OPPORTUNITY_OBJECT = "Opportunity__c"
OPPORTUNITY_AMOUNT_FIELD = "Amount__c"
OPPORTUNITY_CLOSE_DATE_FIELD = "Close_date__c"
OPPORTUNITY_ACCOUNT_FIELD = "Account__c"
OPPORTUNITY_PRODUCT_FAMILY_FIELD = "Product_Family__c"

ORDER_MANAGE_OBJECT = "Order_Manage__c"
ORDER_MANAGE_OPPORTUNITY_FIELD = "Opportunity__c"

DISPATCH_SENT_TO_TALLY_FIELD = "Sent_To_Tally__c"
DISPATCH_VOUCHER_STATUS_FIELD = "Voucher_Status__c"
DISPATCH_TALLY_VOUCHER_NUMBER_FIELD = "Tally_Voucher_Number__c"

# Dispatch transport / invoice header fields
DISPATCH_VEHICLE_NO_FIELD = "Vehicle_Number__c"
DISPATCH_DESTINATION_FIELD = "Destination__c"
DISPATCH_FREIGHT_FIELD = "Freight__c"
DISPATCH_VOUCHER_TYPE_FIELD = "Invoice_Voucher_Type__c"
DISPATCH_WAREHOUSE_FIELD = "Warehouses__c"
WAREHOUSE_LOCATION_FIELD = "Location__c"

PRODUCT_HSN_FIELD = "HSN_Code__c"
PRODUCT_FAMILY_FIELD = "Product_Family__c"
PRODUCT_INFO_FIELD = "Product_Info__c"
PRODUCT_LAMINATION_THICKNESS_FIELD = "Lamination_Sheet_Thickness__c"
ORDER_MANAGE_LAMINATION_QTY_FIELD = "Laminates_Qty__c"
ORDER_MANAGE_LAMINATION_RATE_FIELD = "Laminates_Price_per_Sheet__c"
ORDER_MANAGE_LAMINATION_TOTAL_FIELD = "Laminates_Price__c"
ORDER_MANAGE_LAMINATION_ITEM_CODE_FIELD = "Laminates_Item_Code__c"
ORDER_MANAGE_LAMINATION_BRAND_FIELD = "Laminates_Brands__c"
ORDER_MANAGE_LAMINATION_COLOR_FIELD = "Laminates_Color__c"
ORDER_MANAGE_LAMINATION_GRADE_FIELD = "Lamination_Sheet_Grade__c"
ORDER_MANAGE_LAMINATION_THICKNESS_FIELD = "Lamination_Sheet_Thickness__c"
ORDER_MANAGE_FOAM_QTY_FIELD = "Foam_Sheet_Qty__c"
ORDER_MANAGE_FOAM_RATE_FIELD = "Foam_Sheet_Price_per_Qty__c"
ORDER_MANAGE_FIXIT_QTY_FIELD = "Fixit_Quantity__c"
ORDER_MANAGE_FIXIT_RATE_FIELD = "Fixit_Price_per_Qty__c"
ORDER_MANAGE_PRO_CODE_FIELD = "Pro_Code__c"
ORDER_MANAGE_REQUIRED_STOCK_FIELD = "Required_Stock__c"
ORDER_MANAGE_BUNDLE_COUNT_FIELD = "Bundle_Count__c"
ORDER_MANAGE_MTR_FIELD = "Mtr__c"
ORDER_MANAGE_PRODUCT_NAME_FIELD = "UPVC_Product_Name__c"
ORDER_MANAGE_ORDER_NAME_FIELD = "Order_Name__c"
ORDER_MANAGE_PRODUCT_INFO_FIELD = "Product_Info__c"
ORDER_MANAGE_PRODUCT_LOOKUP_FIELD = "Product__c"
PRODUCT_CODE_FIELD = "Product_Code__c"
PRODUCT_OBJECT = "Product__c"
PRODUCT_BASE_PRICE_FIELD = "Base_Price__c"
STOCK_OBJECT = "Stock__c"
STOCK_PRODUCT_NAME_FIELD = "Name__c"
STOCK_PRODUCT_INFO_FIELD = "Product_Info__c"
STOCK_PRODUCT_LOOKUP_FIELD = "Product__c"
STOCK_WAREHOUSE_FIELD = "Warehouse__c"
STOCK_BUNDLES_FIELD = "Bundles__c"
STOCK_BUNDLE_COUNT_FIELD = "Bundle_Count__c"
STOCK_QUANTITY_FIELD = "Quantity__c"
WAREHOUSE_OBJECT = "Warehouse__c"
ORDER_MANAGE_GST_RATE_FIELD = "GST_Rate__c"
