"""Account outstanding: Salesforce Account Id → Tally ledger outstanding (read-only)."""

from __future__ import annotations

import re
from datetime import date
from xml.sax.saxutils import escape

import requests

from config import ACCOUNT_GSTIN_FIELD
from salesforce_service import _run_query
from tally_service import (
    _clean_tally_text,
    _sanitize_tally_xml,
    _tally_post,
    _tally_static_variables,
)
import xml.etree.ElementTree as ET


def _normalize_name(value):

    """Compare Account vs ledger names ignoring spaces/punctuation (R.K vs R.K.)."""

    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = text.rstrip(".")
    # Keep only letters/digits so "R.K Aluminium" == "R.K.Aluminium"
    return re.sub(r"[^a-z0-9]", "", text)


def _normalize_gstin(value):

    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()


def get_account_for_outstanding(account_id):

    account_id = str(account_id or "").strip()
    if not account_id:
        raise ValueError("account_id is required")

    safe_id = account_id.replace("\\", "\\\\").replace("'", "\\'")
    query = f"""
    SELECT Id, Name, {ACCOUNT_GSTIN_FIELD}
    FROM Account
    WHERE Id = '{safe_id}'
    LIMIT 1
    """
    rows = _run_query(query)
    if not rows:
        return None
    row = rows[0]
    return {
        "id": row.get("Id"),
        "name": (row.get("Name") or "").strip(),
        "gstin": (row.get(ACCOUNT_GSTIN_FIELD) or "").strip(),
    }


def _ledger_name_candidates(account_name):

    name = str(account_name or "").strip()
    if not name:
        return []

    dotted = re.sub(r"(\.\w)\s+", r"\1.", name)  # R.K Aluminium → R.K.Aluminium
    no_space = re.sub(r"\s+", "", dotted)  # R.K.AluminiumTraders

    candidates = [
        name,
        name.rstrip("."),
        f"{name.rstrip('.')}.",
        dotted,
        dotted.rstrip("."),
        f"{dotted.rstrip('.')}.",
        no_space,
        f"{no_space.rstrip('.')}.",
    ]

    seen = set()
    out = []
    for item in candidates:
        key = item.strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _export_ledger_object(ledger_name):

    static = _tally_static_variables("<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>")
    xml = f"""<ENVELOPE>
<HEADER>
<VERSION>1</VERSION>
<TALLYREQUEST>Export</TALLYREQUEST>
<TYPE>Object</TYPE>
<SUBTYPE>Ledger</SUBTYPE>
<ID TYPE="Name">{escape(ledger_name)}</ID>
</HEADER>
<BODY>
<DESC>
{static}
<FETCHLIST>
<FETCH>Name</FETCH>
<FETCH>Parent</FETCH>
<FETCH>ClosingBalance</FETCH>
<FETCH>GSTIN</FETCH>
<FETCH>PartyGSTIN</FETCH>
<FETCH>IncomeTaxNumber</FETCH>
</FETCHLIST>
</DESC>
</BODY>
</ENVELOPE>"""
    response = _tally_post(xml)
    return _sanitize_tally_xml(response.text)


def _parse_ledger_identity(response_text):

    try:
        root = ET.fromstring(response_text)
    except ET.ParseError:
        return None

    ledger = None
    for node in root.findall(".//LEDGER"):
        # Skip CMPINFO counters like <LEDGER>0</LEDGER>
        name_attr = (node.get("NAME") or node.get("REQNAME") or "").strip()
        name_child = _clean_tally_text(node.findtext("NAME") or "")
        if name_attr or (name_child and not name_child.isdigit()):
            ledger = node
            break

    if ledger is None:
        return None

    name = _clean_tally_text(
        ledger.get("NAME")
        or ledger.get("REQNAME")
        or ledger.findtext("NAME")
        or ""
    )
    if not name or name.isdigit():
        return None

    gstin = _clean_tally_text(
        ledger.findtext("GSTIN")
        or ledger.findtext("PARTYGSTIN")
        or ""
    )
    if not gstin:
        match = re.search(
            r"<GSTIN[^>]*>\s*([0-9A-Z]{15})\s*</GSTIN>",
            response_text,
            re.I,
        )
        if match:
            gstin = match.group(1)

    closing_raw = _clean_tally_text(ledger.findtext("CLOSINGBALANCE") or "")
    try:
        closing = float(str(closing_raw).replace(",", "") or 0)
    except ValueError:
        closing = 0.0

    parent = _clean_tally_text(ledger.findtext("PARENT") or "")
    return {
        "name": name,
        "gstin": gstin,
        "closing_balance": closing,
        "parent": parent,
    }


def find_party_ledger(account_name, gstin):

    """Match Tally ledger by Name (tolerant) + GSTIN."""

    want_gst = _normalize_gstin(gstin)
    want_name = _normalize_name(account_name)
    if not want_name:
        return {
            "success": False,
            "message": "Account Name is blank in Salesforce.",
            "ledger": None,
        }
    if not want_gst:
        return {
            "success": False,
            "message": "Account GST is blank in Salesforce. GST is required to match Tally ledger.",
            "ledger": None,
        }

    tried = []
    for candidate in _ledger_name_candidates(account_name):
        tried.append(candidate)
        try:
            text = _export_ledger_object(candidate)
        except requests.RequestException as exc:
            return {
                "success": False,
                "message": f"Tally request failed: {exc}",
                "ledger": None,
            }

        if "Could not find" in text or "<ERROR" in text.upper() and "LEDGER" not in text:
            continue

        identity = _parse_ledger_identity(text)
        if not identity or not identity.get("gstin"):
            native = _export_ledger_native_identity(candidate)
            if native:
                if identity:
                    if not identity.get("gstin"):
                        identity["gstin"] = native.get("gstin")
                    if identity.get("closing_balance") in (None, 0, 0.0) and native.get(
                        "closing_balance"
                    ):
                        identity["closing_balance"] = native.get("closing_balance")
                    if not identity.get("parent"):
                        identity["parent"] = native.get("parent")
                    # Prefer Object closing when native dump has 0
                    if abs(float(identity.get("closing_balance") or 0)) < 0.01:
                        if abs(float(native.get("closing_balance") or 0)) >= 0.01:
                            identity["closing_balance"] = native.get("closing_balance")
                else:
                    identity = native
        if not identity:
            continue

        ledger_gst = _normalize_gstin(identity.get("gstin"))
        ledger_name_n = _normalize_name(identity.get("name"))
        if ledger_name_n != want_name:
            continue
        if not ledger_gst:
            # Name matched but GST not exported yet — try next name variant
            continue
        if ledger_gst != want_gst:
            return {
                "success": False,
                "message": (
                    f"Ledger '{identity.get('name')}' found but GST mismatch. "
                    f"Salesforce={gstin}, Tally={identity.get('gstin') or '(blank)'}."
                ),
                "ledger": identity,
                "tried_names": tried,
            }

        return {
            "success": True,
            "message": f"Matched ledger '{identity.get('name')}' with GST {identity.get('gstin')}.",
            "ledger": identity,
            "tried_names": tried,
        }

    return {
        "success": False,
        "message": (
            f"No Tally ledger matched Account '{account_name}' with GST '{gstin}'. "
            f"Tried: {', '.join(tried)}."
        ),
        "ledger": None,
        "tried_names": tried,
    }


def _export_ledger_native_identity(ledger_name):

    """Single-ledger identity dump (safe — filtered to one name)."""

    static = _tally_static_variables("<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>")
    xml = f"""<ENVELOPE>
<HEADER>
<VERSION>1</VERSION>
<TALLYREQUEST>Export</TALLYREQUEST>
<TYPE>Collection</TYPE>
<ID>LedgerIdent</ID>
</HEADER>
<BODY>
<DESC>
{static}
<TDL>
<TDLMESSAGE>
<COLLECTION NAME="LedgerIdent" ISMODIFY="No">
<TYPE>Ledger</TYPE>
<NATIVEMETHOD>*</NATIVEMETHOD>
<FILTER>Nm</FILTER>
</COLLECTION>
<SYSTEM TYPE="Formulae" NAME="Nm">$$IsEqual:$Name:"{escape(ledger_name)}"</SYSTEM>
</TDLMESSAGE>
</TDL>
</DESC>
</BODY>
</ENVELOPE>"""
    try:
        response = _tally_post(xml)
        text = _sanitize_tally_xml(response.text)
    except requests.RequestException:
        return None

    identity = _parse_ledger_identity(text)
    if identity and not identity.get("gstin"):
        match = re.search(
            r"<GSTIN[^>]*>\s*([0-9A-Z]{15})\s*</GSTIN>",
            text,
            re.I,
        )
        if match:
            identity["gstin"] = match.group(1)
    return identity


def _parse_bills_receivable(response_text, ledger_name):

    want = _normalize_name(ledger_name)
    pattern = re.compile(
        r"<BILLFIXED>\s*"
        r"<BILLDATE>(.*?)</BILLDATE>\s*"
        r"<BILLREF>(.*?)</BILLREF>\s*"
        r"<BILLPARTY>(.*?)</BILLPARTY>\s*"
        r"</BILLFIXED>\s*"
        r"<BILLCL>(.*?)</BILLCL>\s*"
        r"<BILLDUE>(.*?)</BILLDUE>\s*"
        r"<BILLOVERDUE>(.*?)</BILLOVERDUE>",
        re.S | re.I,
    )
    bills = []
    for match in pattern.finditer(response_text or ""):
        party = _clean_tally_text(match.group(3))
        if _normalize_name(party) != want:
            continue
        try:
            amount = float(str(match.group(4)).replace(",", "") or 0)
        except ValueError:
            amount = 0.0
        try:
            overdue = int(float(str(match.group(6)).replace(",", "") or 0))
        except ValueError:
            overdue = 0
        bills.append({
            "date": _clean_tally_text(match.group(1)),
            "ref": _clean_tally_text(match.group(2)),
            "party": party,
            "amount": amount,
            "due_on": _clean_tally_text(match.group(5)),
            "overdue_days": max(overdue, 0),
        })
    return bills


def _format_inr_amount(amount):

    """Indian numbering with ₹: 702075 -> ₹7,02,075.00 ; 105000 -> ₹1,05,000.00."""

    try:
        value = abs(float(amount or 0))
    except (TypeError, ValueError):
        value = 0.0

    rounded = f"{value:.2f}"
    whole, frac = rounded.split(".")
    if len(whole) <= 3:
        return f"₹{whole}.{frac}"

    last3 = whole[-3:]
    rest = whole[:-3]
    parts = []
    while rest:
        parts.append(rest[-2:])
        rest = rest[:-2]
    parts.reverse()
    return f"₹{','.join(parts)},{last3}.{frac}"


def get_party_outstanding_from_tally(ledger_name, closing_balance=None):

    """Total outstanding amount + max overdue days for one party ledger."""

    # Overall outstanding = abs(ledger closing). Bills Receivable drives days.
    amount = abs(float(closing_balance or 0))

    static = _tally_static_variables(
        "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>\n"
        f"<SVFROMDATE>20000401</SVFROMDATE>\n"
        f"<SVTODATE>{date.today().strftime('%Y%m%d')}</SVTODATE>"
    )
    xml = f"""<ENVELOPE>
<HEADER>
<TALLYREQUEST>Export Data</TALLYREQUEST>
</HEADER>
<BODY>
<EXPORTDATA>
<REQUESTDESC>
<REPORTNAME>Bills Receivable</REPORTNAME>
{static}
</REQUESTDESC>
</EXPORTDATA>
</BODY>
</ENVELOPE>"""

    response = _tally_post(xml)
    text = _sanitize_tally_xml(response.text)
    bills = _parse_bills_receivable(text, ledger_name)

    bill_total = sum(abs(float(b.get("amount") or 0)) for b in bills)
    max_days = max((int(b.get("overdue_days") or 0) for b in bills), default=0)

    # If closing wasn't available, fall back to bill sum
    if amount <= 0 and bill_total > 0:
        amount = bill_total

    return {
        "outstanding_amount": round(amount, 2),
        "outstanding_days": max_days,
        "bill_count": len(bills),
        "bill_total": round(bill_total, 2),
        "bills": bills,
    }


def get_account_outstanding(account_id):

    """Read-only: Account Id → match Tally ledger → outstanding amount + days."""

    account = get_account_for_outstanding(account_id)
    if not account:
        return {
            "success": False,
            "message": f"Salesforce Account not found: {account_id}",
        }

    match = find_party_ledger(account["name"], account["gstin"])
    if not match.get("success"):
        return {
            "success": False,
            "message": match.get("message"),
        }

    ledger = match["ledger"]
    outstanding = get_party_outstanding_from_tally(
        ledger["name"],
        closing_balance=ledger.get("closing_balance"),
    )

    return {
        "success": True,
        "outstanding_amount": _format_inr_amount(
            outstanding["outstanding_amount"]
        ),
        "outstanding_days": outstanding["outstanding_days"],
    }
