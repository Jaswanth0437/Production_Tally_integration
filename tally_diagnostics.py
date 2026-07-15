import html
import os
import re
from glob import glob


def extract_tally_error(response_text):

    for tag in ("LINEERROR", "EXCEPTION", "ERRMSG", "MESSAGE"):
        match = re.search(rf"<{tag}>(.*?)</{tag}>", response_text, re.DOTALL | re.IGNORECASE)
        if match and match.group(1).strip():
            return html.unescape(match.group(1).strip())

    if re.search(r"<EXCEPTIONS>\s*([1-9]\d*)\s*</EXCEPTIONS>", response_text):
        return None

    return None


def parse_tally_response(response_text):

    summary = {}
    for tag in (
        "CREATED", "ALTERED", "DELETED", "IGNORED", "ERRORS",
        "CANCELLED", "EXCEPTIONS", "COMBINED",
    ):
        match = re.search(rf"<{tag}>(\d+)</{tag}>", response_text)
        if match:
            summary[tag.lower()] = int(match.group(1))

    return {
        "summary": summary,
        "line_error": extract_tally_error(response_text),
        "raw_response": response_text.strip(),
    }


def _tally_imp_search_paths():

    paths = [
        r"C:\Program Files\TallyPrime\tally.imp",
        r"C:\Program Files (x86)\TallyPrime\tally.imp",
        r"C:\TallyPrime\tally.imp",
        r"C:\Tally.ERP9\tally.imp",
        os.path.expandvars(r"%LOCALAPPDATA%\Tally\tally.imp"),
        os.path.expandvars(r"%APPDATA%\Tally\tally.imp"),
    ]

    for pattern in (
        r"C:\Program Files\Tally*\tally.imp",
        r"C:\Program Files (x86)\Tally*\tally.imp",
        os.path.expandvars(r"%LOCALAPPDATA%\Tally*\tally.imp"),
    ):
        paths.extend(glob(pattern))

    seen = set()
    for path in paths:
        normalized = os.path.normcase(path)
        if normalized not in seen:
            seen.add(normalized)
            yield path


def read_tally_imp_tail(lines=80):

    for path in _tally_imp_search_paths():
        if not os.path.isfile(path):
            continue

        with open(path, "r", encoding="utf-16", errors="replace") as handle:
            content = handle.readlines()

        return {
            "path": path,
            "lines": [line.rstrip() for line in content[-lines:]],
        }

    return {
        "path": None,
        "lines": [],
        "hint": (
            "tally.imp not found automatically. In TallyPrime, press F1 > "
            "Settings > Configuration > Data Configuration and note the data folder, "
            "or search for tally.imp under C:\\Program Files\\TallyPrime."
        ),
    }
