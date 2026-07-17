"""Create missing Tally stock items from Salesforce Product__c records."""

from __future__ import annotations

import re

from config import TALLY_DEFAULT_HSN_CODE
from salesforce_service import get_products_by_ids
from tally_diagnostics import parse_tally_response
from tally_service import (
    _normalize_part_no,
    create_stock_item,
    ensure_stock_group,
    find_tally_stock_by_name,
    find_tally_stock_by_part_no,
)

LAMINATION_FAMILY = "Lamination Sheet"


def _normalize_text(value):

    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _is_lamination(family):

    return _normalize_text(family) == _normalize_text(LAMINATION_FAMILY)


def _format_thickness_group(thickness):

    """Normalize SF thickness to a Tally subgroup name: 1MM, 2.5MM, 3MM."""

    text = str(thickness or "").strip()
    if not text:
        return ""
    text = re.sub(r"\s+", "", text.upper())
    if re.fullmatch(r"[\d.]+", text):
        text = f"{text}MM"
    return text


def _tally_stock_name(family, product_info):

    """Tally Name = Product Family - Product Info."""

    family = str(family or "").strip()
    product_info = str(product_info or "").strip()
    if family and product_info:
        return f"{family} - {product_info}"
    return product_info or family


def _resolve_groups(product):

    """Return (tally_name, stock_parent, groups_to_ensure, error)."""

    family = product["family"]
    info = product.get("product_info") or ""
    tally_name = _tally_stock_name(family, info)

    if _is_lamination(family):
        thickness = _format_thickness_group(product.get("thickness"))
        if not thickness:
            return (
                tally_name,
                None,
                [],
                "Lamination_Sheet_Thickness__c is required for Lamination Sheet",
            )
        groups = [
            (family, None),
            (thickness, family),
        ]
        return tally_name, thickness, groups, None

    groups = [(family, None)]
    return tally_name, family, groups, None


def _find_stock_by_product_code(code):

    """Existence check by product code only (Part No or stock Name == code)."""

    code = str(code or "").strip()
    if not code:
        return None

    by_part = find_tally_stock_by_part_no(code)
    if by_part:
        return by_part

    # Legacy: stock item named as the product code
    by_name = find_tally_stock_by_name(code)
    if by_name and _normalize_part_no(by_name.get("name")) == _normalize_part_no(code):
        return by_name

    return None


def sync_products_to_tally(product_id):

    """Fetch one SF product by Id and create Tally stock item when missing.

    Mapping:
      Product_Info__c → "{Product Family} - {Product Info}"
      Product_Code__c → Part No (MAILINGNAME)
      Product_Family__c → root stock group
      HSN_Code__c → HSN/SAC

    Lamination Sheet only:
      root = Lamination Sheet
      child group = thickness (1MM / 2.5MM / 3MM)
      stock Under = thickness group

    Existence: product code only. If code already in Tally → error, do not create.
    """

    product_id = str(product_id or "").strip()
    if not product_id:
        return {
            "success": False,
            "message": "product_id is required (single Product__c Id).",
            "created": [],
            "skipped": [],
            "errors": [],
        }

    products = get_products_by_ids([product_id])
    if not products:
        return {
            "success": False,
            "message": f"Product__c not found in Salesforce: {product_id}",
            "created": [],
            "skipped": [],
            "errors": [{"product_id": product_id, "reason": "Product__c not found in Salesforce"}],
        }

    errors = []
    created = []
    skipped = []

    product = products[0]
    pid = product["id"]
    name = product.get("name") or ""
    info = product.get("product_info") or ""
    code = product["code"]
    family = product["family"]
    thickness = product.get("thickness") or ""
    hsn = product["hsn"] or TALLY_DEFAULT_HSN_CODE

    if not info or not code or not family:
        missing = []
        if not info:
            missing.append("Product_Info__c")
        if not code:
            missing.append("Product_Code__c")
        if not family:
            missing.append("Product_Family__c")
        return {
            "success": False,
            "message": f"Missing required field(s): {', '.join(missing)}",
            "created": [],
            "skipped": [],
            "errors": [{
                "product_id": pid,
                "name": name,
                "product_info": info,
                "code": code,
                "family": family,
                "reason": f"Missing required field(s): {', '.join(missing)}",
            }],
        }

    tally_name, stock_parent, groups, resolve_error = _resolve_groups(product)
    if resolve_error:
        return {
            "success": False,
            "message": resolve_error,
            "created": [],
            "skipped": [],
            "errors": [{
                "product_id": pid,
                "product_info": info,
                "code": code,
                "family": family,
                "thickness": thickness,
                "reason": resolve_error,
            }],
        }

    try:
        existing = _find_stock_by_product_code(code)
    except Exception as exc:
        return {
            "success": False,
            "message": f"Tally lookup failed: {exc}",
            "created": [],
            "skipped": [],
            "errors": [{
                "product_id": pid,
                "product_info": info,
                "code": code,
                "family": family,
                "reason": f"Tally lookup failed: {exc}",
            }],
        }

    if existing:
        existing_name = existing.get("name") or ""
        reason = (
            f"This code '{code}' already exists with another product "
            f"('{existing_name}'). Not created."
        )
        return {
            "success": False,
            "message": reason,
            "created": [],
            "skipped": [],
            "errors": [{
                "product_id": pid,
                "product_info": info,
                "tally_name": tally_name,
                "code": code,
                "family": family,
                "tally_name_existing": existing_name,
                "tally_parent": existing.get("parent"),
                "reason": reason,
            }],
        }

    try:
        for group_name, group_parent in groups:
            ensure_stock_group(group_name, parent=group_parent)

        response_text = create_stock_item(
            product_name=tally_name,
            product_code=code,
            parent=stock_parent,
            opening_qty=0,
            hsn_code=hsn,
        )
        parsed = parse_tally_response(response_text)
        summary = parsed.get("summary") or {}
        line_error = parsed.get("line_error")
        created_count = int(summary.get("created") or 0)
        altered_count = int(summary.get("altered") or 0)
        errors_count = int(summary.get("errors") or 0)
        exceptions = int(summary.get("exceptions") or 0)

        if line_error or errors_count or exceptions or (created_count + altered_count) < 1:
            hint = ""
            if exceptions and not line_error:
                hint = (
                    " Check Tally: Data → All Exceptions "
                    "(often Part No conflicts with another stock Name)."
                )
            reason = (line_error or f"Tally did not create stock item (summary={summary})") + hint
            return {
                "success": False,
                "message": reason,
                "created": [],
                "skipped": [],
                "errors": [{
                    "product_id": pid,
                    "product_info": info,
                    "tally_name": tally_name,
                    "code": code,
                    "family": family,
                    "under": stock_parent,
                    "hsn": hsn,
                    "reason": reason,
                }],
            }

        created.append({
            "product_id": pid,
            "product_info": info,
            "tally_name": tally_name,
            "code": code,
            "family": family,
            "under": stock_parent,
            "thickness": thickness if _is_lamination(family) else None,
            "hsn": hsn,
        })
    except Exception as exc:
        return {
            "success": False,
            "message": str(exc),
            "created": [],
            "skipped": [],
            "errors": [{
                "product_id": pid,
                "product_info": info,
                "code": code,
                "family": family,
                "reason": str(exc),
            }],
        }

    return {
        "success": True,
        "message": f"Created stock item '{tally_name}' (code={code}).",
        "created": created,
        "skipped": skipped,
        "errors": errors,
    }
