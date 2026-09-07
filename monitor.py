from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
import yaml
from bs4 import BeautifulSoup
from ddgs import DDGS

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
STATE_PATH = ROOT / "data" / "seen_offers.json"
ENV_PATH = ROOT / ".env"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/127.0 Safari/537.36 BikePriceAlert/1.0"
    ),
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8,es;q=0.7",
}

PRICE_PATTERNS = [
    # 1.699,00 € / 1699,00 EUR / 1,699.00 EUR
    re.compile(
        r"(?<!\d)(\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?|"
        r"\d{4,5}(?:[.,]\d{1,2})?)(?![\d.,])\s*(?:€|EUR)(?![A-Za-z])",
        re.I,
    ),
    # € 1,699.00 / EUR 1699
    re.compile(
        r"(?:€|EUR)\s*(\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?|"
        r"\d{4,5}(?:[.,]\d{1,2})?)(?![\d.,])",
        re.I,
    ),
]


@dataclass
class Offer:
    model: str
    size: str
    price: float
    title: str
    url: str
    domain: str
    availability: str
    source: str


def load_local_env(path: Path = ENV_PATH) -> None:
    """Load simple KEY=VALUE entries without overriding shell/GitHub variables."""
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"offers": {}}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"offers": {}}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def normalize_domain(url: str) -> str:
    host = urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def domain_allowed(domain: str, allowed: list[str]) -> bool:
    normalized = [str(d).lower().removeprefix("www.").strip(".") for d in allowed]
    return any(domain == d or domain.endswith("." + d) for d in normalized)


def canonicalize_url(url: str) -> str:
    """Remove fragments and common tracking parameters used by search results."""
    parsed = urlparse(url)
    tracking_names = {
        "fbclid",
        "gclid",
        "msclkid",
        "ref",
        "source",
    }
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in tracking_names
    ]
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path,
            parsed.params,
            urlencode(query, doseq=True),
            "",
        )
    )


def parse_price_number(raw: str) -> float | None:
    s = raw.strip().replace("\xa0", "").replace(" ", "")
    if not s:
        return None

    # European style: 1.699,00
    if "." in s and "," in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # The last separator is decimal only when followed by one/two digits.
        parts = s.split(",")
        if len(parts[-1]) in {1, 2}:
            s = "".join(parts[:-1]) + "." + parts[-1]
        else:
            s = "".join(parts)
    elif s.count(".") > 1:
        parts = s.split(".")
        if len(parts[-1]) in {1, 2}:
            s = "".join(parts[:-1]) + "." + parts[-1]
        else:
            s = "".join(parts)
    elif "." in s:
        left, right = s.rsplit(".", 1)
        if len(right) == 3 and left.isdigit():
            s = left + right

    try:
        return float(s)
    except ValueError:
        return None


def extract_prices(text: str, min_price: float, max_reasonable: float = 15000) -> list[float]:
    values: list[float] = []
    for pat in PRICE_PATTERNS:
        for m in pat.finditer(text):
            value = parse_price_number(m.group(1))
            if value is not None and min_price <= value <= max_reasonable:
                values.append(round(value, 2))
    return sorted(set(values))


def iter_jsonld(node: Any):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from iter_jsonld(v)
    elif isinstance(node, list):
        for item in node:
            yield from iter_jsonld(item)


def jsonld_offer_data(
    soup: BeautifulSoup,
    min_price: float,
    aliases: list[str] | None = None,
) -> tuple[list[float], str]:
    prices: list[float] = []
    availability = "unknown"

    def collect_offer(obj: dict[str, Any]) -> None:
        nonlocal availability
        for field in ("price", "lowPrice"):
            if field in obj:
                price = parse_price_number(str(obj[field]))
                if price is not None and min_price <= price <= 15000:
                    prices.append(round(price, 2))
        avail = str(obj.get("availability", "")).lower()
        if "outofstock" in avail or "soldout" in avail:
            availability = "out_of_stock"
        elif "instock" in avail or "limitedavailability" in avail:
            availability = "in_stock"

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text(" ", strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue

        objects = list(iter_jsonld(data))
        matching_products: list[dict[str, Any]] = []
        for obj in objects:
            typ = obj.get("@type")
            if isinstance(typ, list):
                types = {str(x).lower() for x in typ}
            else:
                types = {str(typ).lower()} if typ else set()

            if "product" in types and (
                aliases is None or alias_present(str(obj.get("name", "")), aliases)
            ):
                matching_products.append(obj)

        if matching_products:
            for product in matching_products:
                for obj in iter_jsonld(product.get("offers", [])):
                    typ = obj.get("@type")
                    types = typ if isinstance(typ, list) else [typ]
                    if any(str(value).lower() in {"offer", "aggregateoffer"} for value in types):
                        collect_offer(obj)
        elif aliases is None:
            # Some pages expose a standalone Offer without a parent Product.
            for obj in objects:
                typ = obj.get("@type")
                types = typ if isinstance(typ, list) else [typ]
                if any(str(value).lower() in {"offer", "aggregateoffer"} for value in types):
                    collect_offer(obj)

    return sorted(set(prices)), availability


def fetch_page(url: str) -> tuple[str, BeautifulSoup | None]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=18, allow_redirects=True)
        r.raise_for_status()
        # Avoid huge/binary responses.
        content_type = r.headers.get("content-type", "")
        if "text/html" not in content_type and "application/xhtml" not in content_type:
            return "", None
        text = r.text[:2_000_000]
        soup = BeautifulSoup(text, "html.parser")
        return text, soup
    except Exception as exc:
        print(f"[warn] fetch failed {url}: {exc}", file=sys.stderr)
        return "", None


def visible_text(soup: BeautifulSoup | None) -> str:
    if soup is None:
        return ""
    # Do not mutate ``soup``: price parsing still needs its JSON-LD scripts.
    ignored = {"script", "style", "noscript", "svg"}
    return " ".join(
        value.strip()
        for value in soup.find_all(string=True)
        if value.strip() and value.parent and value.parent.name not in ignored
    )


def alias_present(text: str, aliases: list[str]) -> bool:
    low = text.lower()
    return any(alias.lower() in low for alias in aliases)


def product_identity_text(soup: BeautifulSoup | None) -> str:
    """Return product-name fields, excluding recommendations and general body copy."""
    if soup is None:
        return ""

    values: list[str] = []
    if soup.title:
        values.append(soup.title.get_text(" ", strip=True))
    for node in soup.select(
        'h1, meta[property="og:title"], meta[name="twitter:title"]'
    ):
        raw = node.get("content") or node.get_text(" ", strip=True)
        if raw:
            values.append(str(raw))

    return " ".join(values)


def size_evidence_text(
    soup: BeautifulSoup | None,
    page_text: str,
    title: str,
    snippet: str,
) -> str:
    """Add form/variant metadata so a standalone option such as ``L`` has context."""
    values = [title, snippet, page_text]
    if soup is None:
        return " ".join(values)

    selector = (
        "select, option, button, label, "
        '[class*="size" i], [id*="size" i], '
        '[class*="tamanho" i], [id*="tamanho" i], '
        '[class*="talla" i], [id*="talla" i]'
    )
    for node in soup.select(selector):
        parts = ["size:", node.get_text(" ", strip=True)]
        for attr in ("value", "data-size", "aria-label", "title", "class", "id"):
            value = node.get(attr)
            if isinstance(value, list):
                parts.extend(str(item) for item in value)
            elif value:
                parts.append(str(value))
        values.append(" ".join(parts))
    return " ".join(values)


def target_size_matches(text: str, sizes: list[str], out_terms: list[str]) -> list[str]:
    """
    Conservative size detection:
      * Sizes must appear near size/talla/tamanho terminology.
      * ``size_evidence_text`` adds that context to standalone variant controls.
      * A match is rejected when nearby text explicitly says out of stock.
    """
    flat = re.sub(r"\s+", " ", html.unescape(text))
    low = flat.lower()
    found: list[str] = []

    for size in sizes:
        s = size.strip()
        boundary_start = r"(?<![A-Za-z0-9])" if not s.isdigit() else r"(?<!\d)"
        boundary_end = r"(?![A-Za-z0-9])" if not s.isdigit() else r"(?!\d)"
        size_word = r"(?:tamanho|talla|taille|taglia|size|frame|quadro|tam)"
        patterns = [
            re.compile(
                rf"{size_word}.{{0,40}}?{boundary_start}{re.escape(s)}{boundary_end}",
                re.I,
            ),
            re.compile(
                rf"{boundary_start}{re.escape(s)}{boundary_end}.{{0,40}}?{size_word}",
                re.I,
            ),
        ]

        accepted = False
        for pat in patterns:
            for m in pat.finditer(flat):
                start = max(0, m.start() - 80)
                end = min(len(flat), m.end() + 160)
                context = flat[start:end].lower()

                # If an explicit OOS phrase is very close to this size, reject this occurrence.
                if any(term.lower() in context for term in out_terms):
                    # Some pages list many sizes then a global "out of stock"; be conservative.
                    continue
                accepted = True
                break
            if accepted:
                break

        if accepted:
            found.append(s)

    return found


def page_availability(
    soup: BeautifulSoup | None,
    structured_availability: str,
    out_terms: list[str],
) -> str:
    """Read explicit, product-level stock labels without treating recommendations as global."""
    if structured_availability != "unknown" or soup is None:
        return structured_availability

    selectors = (
        '[itemprop="availability"]',
        'meta[property="product:availability"]',
        '[class*="availability"]',
        '[id*="availability"]',
        '[class*="stock"]',
        '[id*="stock"]',
        'button[name*="add"]',
        'button[class*="cart"]',
    )
    labels: list[str] = []
    for node in soup.select(", ".join(selectors)):
        raw = node.get("content") or node.get("href") or node.get_text(" ", strip=True)
        if raw:
            labels.append(str(raw).lower())

    heading = soup.select_one("h1")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if heading:
        labels.append(heading.get_text(" ", strip=True).lower())
    if title:
        labels.append(title.lower())

    has_out = any(term.lower() in label for label in labels for term in out_terms)
    in_stock_patterns = (
        re.compile(r"\bin stock\b", re.I),
        re.compile(r"\bem stock\b", re.I),
        re.compile(r"\ben stock\b", re.I),
        re.compile(r"(?<!in)\bdispon[ií]vel\b", re.I),
        re.compile(r"(?<!un)\bavailable\b", re.I),
    )
    has_in = any(pattern.search(label) for label in labels for pattern in in_stock_patterns)

    if has_out and not has_in:
        return "out_of_stock"
    if has_in and not has_out:
        return "in_stock"
    return "unknown"


def choose_price(
    page_html: str,
    page_text: str,
    snippet: str,
    soup: BeautifulSoup | None,
    min_price: float,
    aliases: list[str] | None = None,
) -> tuple[float | None, str, str]:
    json_prices: list[float] = []
    availability = "unknown"
    if soup is not None:
        json_prices, availability = jsonld_offer_data(soup, min_price, aliases)

        # HTML meta fallback
        for el in soup.select(
            '[itemprop="price"], meta[property="product:price:amount"], '
            'meta[property="og:price:amount"]'
        ):
            raw = el.get("content") or el.get("value") or el.get_text(" ", strip=True)
            p = parse_price_number(str(raw))
            if p is not None and min_price <= p <= 15000:
                json_prices.append(round(p, 2))
                # The first semantic price is normally the primary product;
                # later values often belong to recommendation cards.
                break

    if json_prices:
        return min(json_prices), "structured_data", availability

    # Prefer visible product page text over raw HTML to reduce false values.
    for label, corpus in [
        ("page_text", page_text),
        ("search_snippet", snippet),
        ("page_html", page_html),
    ]:
        prices = extract_prices(corpus, min_price=min_price)
        if prices:
            return min(prices), label, availability

    return None, "none", availability


def search_model(model_cfg: dict[str, Any], cfg: dict[str, Any]) -> list[Offer]:
    model_name = model_cfg["name"]
    aliases = model_cfg["aliases"]
    sizes = [str(s) for s in model_cfg["sizes"]]
    allowed = cfg["allowed_domains"]
    out_terms = cfg["out_of_stock_terms"]
    min_price = float(cfg["min_plausible_price_eur"])
    max_results = int(cfg.get("max_results_per_model", 15))

    # Use the first alias as the main search phrase; size and Portugal terms improve relevance.
    size_query = " ".join(sizes[:4])
    q = f'"{aliases[0]}" {size_query} preço bicicleta Portugal'
    print(f"[search] {q}")

    offers: list[Offer] = []
    try:
        results = list(
            DDGS().text(
                q,
                region="pt-pt",
                safesearch="off",
                max_results=max_results,
            )
        )
    except Exception as exc:
        print(f"[warn] search failed for {model_name}: {exc}", file=sys.stderr)
        return offers

    for result in results or []:
        url = canonicalize_url(result.get("href") or result.get("url") or "")
        title = result.get("title") or ""
        snippet = result.get("body") or result.get("snippet") or ""
        if not url.startswith(("http://", "https://")):
            continue

        domain = normalize_domain(url)
        if not domain_allowed(domain, allowed):
            continue

        page_html, soup = fetch_page(url)
        page_text = visible_text(soup)

        # The model must identify the actual product, not a recommendation on a
        # category page that happens to contain the alias and a cheaper item.
        if not alias_present(product_identity_text(soup), aliases):
            continue

        matched_sizes = target_size_matches(
            size_evidence_text(soup, page_text, title, snippet), sizes, out_terms
        )
        if not matched_sizes:
            continue

        price, source, availability = choose_price(
            page_html, page_text, snippet, soup, min_price, aliases
        )
        if price is None:
            continue
        availability = page_availability(soup, availability, out_terms)

        for size in matched_sizes:
            offers.append(
                Offer(
                    model=model_name,
                    size=size,
                    price=price,
                    title=title.strip() or model_name,
                    url=url,
                    domain=domain,
                    availability=availability,
                    source=source,
                )
            )

        time.sleep(0.7)

    # De-duplicate same URL/size, keeping the cheapest price.
    dedup: dict[tuple[str, str], Offer] = {}
    for offer in offers:
        key = (offer.url, offer.size)
        if key not in dedup or offer.price < dedup[key].price:
            dedup[key] = offer
    return list(dedup.values())


def offer_key(offer: Offer) -> str:
    raw = f"{offer.model}|{offer.size}|{offer.url}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def should_alert(offer: Offer, state: dict[str, Any]) -> bool:
    previous = state.get("offers", {}).get(offer_key(offer))
    if not previous or not previous.get("last_alerted"):
        return True

    old_price = float(previous.get("last_alerted_price", previous.get("price", 999999)))
    old_availability = previous.get("availability", "unknown")
    # Alert again for a meaningful price drop or return to stock.
    if offer.price <= old_price - 1.0:
        return True
    if old_availability == "out_of_stock" and offer.availability != "out_of_stock":
        return True
    return False


def update_state(offer: Offer, state: dict[str, Any], alerted: bool) -> None:
    now = datetime.now(timezone.utc).isoformat()
    key = offer_key(offer)
    existing = state.setdefault("offers", {}).get(key, {})
    updated = {
        **existing,
        **asdict(offer),
        "last_alerted": now if alerted else existing.get("last_alerted"),
    }
    if alerted:
        updated["last_alerted_price"] = offer.price

    # Avoid an empty state commit every three hours when nothing changed.
    material_fields = ("model", "size", "price", "title", "url", "domain", "availability")
    if alerted or any(existing.get(field) != updated.get(field) for field in material_fields):
        updated["updated_at"] = now
    else:
        updated["updated_at"] = existing.get("updated_at")
    state["offers"][key] = updated


def send_resend_email(offer: Offer, recipient: str) -> None:
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("RESEND_API_KEY não está configurada.")

    sender = os.getenv("RESEND_FROM", "Bike Alert <onboarding@resend.dev>").strip()
    subject = f"🚨 {offer.model} por {offer.price:.0f} € — tamanho {offer.size}"

    price_pt = f"{offer.price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    availability = (
        "Em stock" if offer.availability == "in_stock"
        else "Disponibilidade indicada na página — confirma a variante antes de pagar"
    )
    body_html = f"""
    <div style="font-family:Arial,sans-serif;max-width:650px;margin:auto">
      <h2>🚴 Nova bicicleta dentro do teu orçamento</h2>
      <table style="border-collapse:collapse;width:100%;font-size:15px">
        <tr><td><b>Modelo</b></td><td>{html.escape(offer.model)}</td></tr>
        <tr><td><b>Tamanho</b></td><td>{html.escape(offer.size)}</td></tr>
        <tr><td><b>Preço</b></td><td><b>{price_pt} €</b></td></tr>
        <tr><td><b>Loja</b></td><td>{html.escape(offer.domain)}</td></tr>
        <tr><td><b>Stock</b></td><td>{html.escape(availability)}</td></tr>
      </table>
      <p style="margin-top:22px">
        <a href="{html.escape(offer.url)}"
           style="background:#111;color:white;padding:12px 18px;text-decoration:none;border-radius:6px">
          Ver oferta
        </a>
      </p>
      <p style="color:#666;font-size:12px">
        Alerta automático. Confirma sempre tamanho, stock e portes para Portugal na loja.
      </p>
    </div>
    """

    payload = {
        "from": sender,
        "to": [recipient],
        "subject": subject,
        "html": body_html,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Idempotency-Key": f"bike-{offer_key(offer)}-{int(offer.price * 100)}",
    }

    r = requests.post(
        "https://api.resend.com/emails",
        headers=headers,
        json=payload,
        timeout=20,
    )
    if r.status_code >= 300:
        raise RuntimeError(f"Resend error {r.status_code}: {r.text}")
    print(f"[email] sent: {subject}")


def main() -> int:
    load_local_env()
    cfg = load_config()
    state = load_state()
    recipient = cfg["recipient"]
    max_alerts = int(cfg.get("max_alerts_per_run", 10))
    max_price = float(cfg["max_price_eur"])
    dry_run = os.getenv("DRY_RUN", "").lower() in {"1", "true", "yes"}
    if not dry_run and not os.getenv("RESEND_API_KEY", "").strip():
        print(
            "[error] RESEND_API_KEY is required unless DRY_RUN=1.",
            file=sys.stderr,
        )
        return 2

    all_offers: list[Offer] = []
    for model_cfg in cfg["models"]:
        all_offers.extend(search_model(model_cfg, cfg))

    all_offers.sort(key=lambda x: (x.price, x.model, x.size))
    available_offers = [
        offer
        for offer in all_offers
        if offer.availability != "out_of_stock" and offer.price <= max_price
    ]
    print(f"[result] {len(available_offers)} qualifying offers found")

    alerts_sent = 0
    alerts_considered = 0
    email_failures = 0
    for offer in all_offers:
        if offer.availability == "out_of_stock" or offer.price > max_price:
            update_state(offer, state, alerted=False)
            continue

        alert = should_alert(offer, state)
        if alert and alerts_considered < max_alerts:
            alerts_considered += 1
            print(
                f"[match] {offer.model} | {offer.size} | "
                f"{offer.price:.2f} € | {offer.domain} | {offer.url}"
            )
            if not dry_run:
                try:
                    send_resend_email(offer, recipient)
                    alerts_sent += 1
                except Exception as exc:
                    print(f"[error] email failed: {exc}", file=sys.stderr)
                    # Keep the prior state so a new/price-drop/restock email is retried.
                    email_failures += 1
                    continue
            else:
                print("[dry-run] email not sent")
            update_state(offer, state, alerted=not dry_run)
        else:
            update_state(offer, state, alerted=False)

    save_state(state)
    print(f"[done] alerts sent: {alerts_sent}")
    return 1 if email_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
