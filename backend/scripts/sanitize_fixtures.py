"""Create minimal, privacy-safe fixtures from authenticated Mercado Livre HTML exports.

The output keeps semantic structures used by parsers while removing scripts, account menus,
hidden tokens, tracking metadata and dynamically generated IDs.
"""

import argparse
import re
from pathlib import Path

from bs4 import BeautifulSoup


def sanitize(source: Path, destination: Path, page: str) -> None:
    soup = BeautifulSoup(source.read_text(encoding="utf-8"), "lxml")
    output = BeautifulSoup("<html lang='pt-BR'><head></head><body></body></html>", "lxml")
    output.title = soup.title.get_text(strip=True) if soup.title else page

    selectors = {
        "hub": ["li.poly-card"],
        "linkbuilder": ["form"],
        "coupons": ["[data-testid='button-create-action']"],
    }
    seen: set[str] = set()
    for selector in selectors[page]:
        for node in soup.select(selector):
            if page == "coupons":
                node = node.find_parent("article") or node.find_parent(class_="andes-card") or node
            serialized = str(node)
            if serialized in seen:
                continue
            seen.add(serialized)
            fragment = BeautifulSoup(serialized, "lxml")
            clean = fragment.body.find() if fragment.body else fragment.find()
            if not clean:
                continue
            for element in clean.find_all(True):
                for attribute in list(element.attrs):
                    if attribute in {"nonce", "id", "data-id", "integrity", "onclick"}:
                        del element.attrs[attribute]
                    elif re.search(r"token|session|user|email|nickname", attribute, re.I):
                        element.attrs[attribute] = "[REDACTED]"
                if element.name == "input" and element.get("type") == "hidden":
                    if element.get("name") != "id":
                        element["value"] = "[REDACTED]"
            output.body.append(clean)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("<!doctype html>\n" + str(output), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("page", choices=["hub", "linkbuilder", "coupons"])
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    sanitize(args.source, args.destination, args.page)


if __name__ == "__main__":
    main()
