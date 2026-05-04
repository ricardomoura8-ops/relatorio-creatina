"""
Price Tracker - SellersFlow
Coleta preços de Amazon, Magalu, Mercado Livre e sites próprios.
Roda via GitHub Actions em schedule definido.
"""

import json
import os
import re
import time
import random
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ─── Configuração de URLs para monitorar ────────────────────────────────────
# Edite links.json para adicionar/remover produtos sem mexer no código.
# ─────────────────────────────────────────────────────────────────────────────

LINKS_FILE = Path(__file__).parent / "links.json"
DATA_FILE = Path(__file__).parent.parent / "data" / "prices.json"
DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ─── Extratores por canal ────────────────────────────────────────────────────

def extract_amazon(soup, url):
    """Extrai preço da Amazon Brasil."""
    # Preço principal (inteiro + frações)
    price = None

    # 1. Tenta o seletor padrão de preço
    whole = soup.select_one(".a-price-whole")
    fraction = soup.select_one(".a-price-fraction")
    if whole:
        raw = whole.get_text(strip=True).replace(".", "").replace(",", "")
        frac = fraction.get_text(strip=True) if fraction else "00"
        try:
            price = float(f"{raw}.{frac}")
        except Exception:
            pass

    # 2. Fallback: corePrice
    if not price:
        el = soup.select_one("#corePrice_feature_div .a-offscreen")
        if el:
            price = parse_brl(el.get_text())

    # 3. Fallback: qualquer .a-offscreen com R$
    if not price:
        for el in soup.select(".a-offscreen"):
            txt = el.get_text()
            if "R$" in txt:
                price = parse_brl(txt)
                break

    title_el = soup.select_one("#productTitle")
    title = title_el.get_text(strip=True) if title_el else ""

    return {"price": price, "title": title}


def extract_magalu(soup, url):
    """Extrai preço do Magalu via JSON-LD ou seletores."""
    price = None
    title = ""

    # JSON-LD (mais confiável)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, list):
                data = data[0]
            if data.get("@type") == "Product":
                title = data.get("name", "")
                offers = data.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0]
                price = float(offers.get("price", 0)) or None
                break
        except Exception:
            continue

    # Fallback seletor CSS
    if not price:
        el = soup.select_one("[data-testid='price-value']")
        if not el:
            el = soup.select_one(".price__value")
        if el:
            price = parse_brl(el.get_text())

    if not title:
        el = soup.select_one("h1")
        title = el.get_text(strip=True) if el else ""

    return {"price": price, "title": title}


def extract_mercadolivre(soup, url):
    """Extrai preço do Mercado Livre."""
    price = None
    title = ""

    # JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, list):
                data = data[0]
            if data.get("@type") == "Product":
                title = data.get("name", "")
                offers = data.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0]
                price = float(offers.get("price", 0)) or None
                break
        except Exception:
            continue

    # Seletores específicos MELI
    if not price:
        for sel in [
            ".andes-money-amount__fraction",
            ".price-tag-fraction",
            "[class*='price-tag-amount']",
        ]:
            el = soup.select_one(sel)
            if el:
                frac_el = soup.select_one(".andes-money-amount__cents, .price-tag-cents")
                raw = el.get_text(strip=True).replace(".", "").replace(",", "")
                cents = frac_el.get_text(strip=True) if frac_el else "00"
                try:
                    price = float(f"{raw}.{cents}")
                    break
                except Exception:
                    pass

    if not title:
        el = soup.select_one("h1")
        title = el.get_text(strip=True) if el else ""

    return {"price": price, "title": title}


def extract_generic(soup, url):
    """
    Extrator genérico para sites próprios.
    Tenta JSON-LD → meta og:price → seletores comuns.
    """
    price = None
    title = ""

    # JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, list):
                data = data[0]
            if data.get("@type") in ("Product", "Offer"):
                title = data.get("name", "")
                offers = data.get("offers", data)
                if isinstance(offers, list):
                    offers = offers[0]
                price = float(offers.get("price", 0)) or None
                break
        except Exception:
            continue

    # meta price (Shopify, WooCommerce)
    if not price:
        for meta_name in ["product:price:amount", "og:price:amount"]:
            el = soup.find("meta", property=meta_name) or soup.find("meta", attrs={"name": meta_name})
            if el and el.get("content"):
                try:
                    price = float(el["content"])
                    break
                except Exception:
                    pass

    # Seletores comuns de e-commerce
    if not price:
        common_selectors = [
            ".price", ".product-price", "#product-price",
            "[data-price]", ".woocommerce-Price-amount",
            ".vtex-store-components-3-x-sellingPrice",
        ]
        for sel in common_selectors:
            el = soup.select_one(sel)
            if el:
                price = parse_brl(el.get_text())
                if price:
                    break

    if not title:
        el = soup.select_one("h1")
        title = el.get_text(strip=True) if el else ""

    return {"price": price, "title": title}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def parse_brl(text: str) -> float | None:
    """Converte 'R$ 1.234,56' → 1234.56"""
    text = re.sub(r"[^\d,\.]", "", text.strip())
    # Remove separador de milhar se houver vírgula como decimal
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except Exception:
        return None


def detect_channel(url: str) -> str:
    if "amazon.com.br" in url or "amazon.com" in url:
        return "amazon"
    if "magalu.com.br" in url or "magazineluiza" in url:
        return "magalu"
    if "mercadolivre.com.br" in url or "mercadolibre.com" in url:
        return "mercadolivre"
    return "site_proprio"


def fetch_page(url: str) -> BeautifulSoup | None:
    try:
        resp = SESSION.get(url, timeout=20, allow_redirects=True)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"  [ERRO] Falha ao buscar {url}: {e}")
        return None


# ─── Core ────────────────────────────────────────────────────────────────────

def scrape_product(entry: dict) -> dict:
    url = entry["url"]
    label = entry.get("label", url[:60])
    channel = detect_channel(url)
    group = entry.get("group", "")

    print(f"  Scraping [{channel}] {label}")

    soup = fetch_page(url)
    if not soup:
        return {**entry, "error": True, "timestamp": now_iso()}

    extractors = {
        "amazon": extract_amazon,
        "magalu": extract_magalu,
        "mercadolivre": extract_mercadolivre,
        "site_proprio": extract_generic,
    }
    result = extractors[channel](soup, url)

    price = result.get("price")
    title = result.get("title") or label

    print(f"    → preço: R${price}" if price else "    → preço: NÃO ENCONTRADO")

    return {
        "id": entry.get("id", url),
        "url": url,
        "label": label,
        "title": title,
        "channel": channel,
        "group": group,
        "price": price,
        "error": price is None,
        "timestamp": now_iso(),
    }


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_links() -> list[dict]:
    if not LINKS_FILE.exists():
        print(f"[AVISO] {LINKS_FILE} não encontrado. Criando exemplo.")
        example = [
            {
                "id": "dux-creatina-300g-amazon",
                "label": "DUX Creatina 300g",
                "group": "DUX Nutrition",
                "url": "https://www.amazon.com.br/dp/B08XYZ1234"
            }
        ]
        LINKS_FILE.write_text(json.dumps(example, indent=2, ensure_ascii=False))
        return example
    return json.loads(LINKS_FILE.read_text())


def load_existing_data() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text())
        except Exception:
            pass
    return {"products": {}, "history": {}}


def save_data(data: dict):
    DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    print(f"[OK] Dados salvos em {DATA_FILE}")


def run():
    print(f"\n{'='*50}")
    print(f"Price Tracker - SellersFlow")
    print(f"Iniciando coleta: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"{'='*50}\n")

    links = load_links()
    data = load_existing_data()

    print(f"Total de produtos: {len(links)}\n")

    for entry in links:
        result = scrape_product(entry)
        pid = result["id"]

        # Atualiza snapshot atual
        data["products"][pid] = result

        # Acumula histórico
        if pid not in data["history"]:
            data["history"][pid] = []
        if result.get("price") is not None:
            data["history"][pid].append({
                "price": result["price"],
                "timestamp": result["timestamp"],
            })
            # Mantém últimas 500 leituras por produto
            data["history"][pid] = data["history"][pid][-500:]

        # Delay anti-throttle
        time.sleep(random.uniform(2.0, 5.0))

    data["last_updated"] = now_iso()
    data["total_products"] = len(links)
    data["valid_readings"] = sum(1 for p in data["products"].values() if not p.get("error"))

    save_data(data)
    print(f"\nColeta finalizada. {data['valid_readings']}/{len(links)} leituras válidas.")


if __name__ == "__main__":
    run()
