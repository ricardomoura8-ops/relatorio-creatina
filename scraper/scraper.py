"""
Price Tracker v3 — SellersFlow
================================
Amazon      → HTML scraping (sem dependência externa)
Site Próprio → JSON-LD (sem dependência externa)
MELI        → API oficial /products/{id}/items (token OAuth2)
Magalu      → ScraperAPI proxy (opcional)

BuyBox retornado em todos os canais.

Env vars (GitHub Secrets):
  MELI_TOKEN        → access_token OAuth2
  MELI_REFRESH_TOKEN → refresh_token (renovação automática)
  MELI_CLIENT_ID    → client_id do app
  MELI_CLIENT_SECRET → client_secret do app
  SCRAPERAPI_KEY    → opcional, para Magalu
"""

import json, os, re, time, random, sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup

# ── CONFIG ────────────────────────────────────────────────────
LINKS_FILE = Path(__file__).parent / "links.json"
DATA_FILE  = Path(__file__).parent.parent / "data" / "prices.json"
DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

MELI_TOKEN         = os.getenv("MELI_TOKEN", "")
MELI_REFRESH_TOKEN = os.getenv("MELI_REFRESH_TOKEN", "")
MELI_CLIENT_ID     = os.getenv("MELI_CLIENT_ID", "")
MELI_CLIENT_SECRET = os.getenv("MELI_CLIENT_SECRET", "")
SCRAPERAPI_KEY     = os.getenv("SCRAPERAPI_KEY", "")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})

# ── TOKEN MANAGEMENT ─────────────────────────────────────────
_meli_token = MELI_TOKEN

def meli_headers():
    return {"Authorization": f"Bearer {_meli_token}"}

def refresh_meli_token():
    global _meli_token
    if not all([MELI_REFRESH_TOKEN, MELI_CLIENT_ID, MELI_CLIENT_SECRET]):
        return False
    try:
        r = requests.post(
            "https://api.mercadolibre.com/oauth/token",
            data={
                "grant_type":    "refresh_token",
                "client_id":     MELI_CLIENT_ID,
                "client_secret": MELI_CLIENT_SECRET,
                "refresh_token": MELI_REFRESH_TOKEN,
            },
            timeout=15
        )
        if r.status_code == 200:
            _meli_token = r.json()["access_token"]
            print(f"  [MELI] Token renovado com sucesso")
            return True
    except Exception as e:
        print(f"  [MELI] Falha ao renovar token: {e}")
    return False

# ── HELPERS ───────────────────────────────────────────────────
def parse_brl(text):
    text = re.sub(r"[^\d,\.]", "", (text or "").strip())
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try: return round(float(text), 2)
    except: return None

def detect_channel(url):
    if "amazon.com" in url:        return "amazon"
    if "magazineluiza" in url:     return "magalu"
    if "mercadolivre" in url or "mercadolibre" in url: return "mercadolivre"
    return "site_proprio"

def fetch_page(url):
    try:
        r = SESSION.get(url, timeout=20, allow_redirects=True)
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser")
    except: return None

def now_iso():
    return datetime.now(timezone.utc).isoformat()

# ── AMAZON ────────────────────────────────────────────────────
def scrape_amazon(url):
    soup = fetch_page(url)
    if not soup:
        return {"price": None, "error": "FETCH_FAILED"}

    price = None
    whole = soup.select_one(".a-price-whole")
    frac  = soup.select_one(".a-price-fraction")
    if whole:
        raw = whole.get_text(strip=True).replace(".", "").replace(",", "")
        f   = frac.get_text(strip=True) if frac else "00"
        try: price = round(float(f"{raw}.{f}"), 2)
        except: pass

    if not price:
        for sel in ["#corePrice_feature_div .a-offscreen", "#priceblock_ourprice", "#priceblock_dealprice"]:
            el = soup.select_one(sel)
            if el:
                price = parse_brl(el.get_text())
                if price: break

    if not price:
        for el in soup.select(".a-offscreen"):
            if "R$" in el.get_text():
                price = parse_brl(el.get_text())
                if price: break

    # BuyBox seller
    buybox_seller = None
    for sel in [
        "#sellerProfileTriggerId",
        "#merchant-info a",
        ".tabular-buybox-text[tabindex] a",
        "#soldByThirdParty a",
    ]:
        el = soup.select_one(sel)
        if el:
            buybox_seller = el.get_text(strip=True)[:80]
            break

    if not buybox_seller:
        for el in soup.select(".tabular-buybox-text, #merchant-info"):
            txt = el.get_text(strip=True)
            if txt and len(txt) < 100:
                buybox_seller = txt
                break

    title_el = soup.select_one("#productTitle")
    title = title_el.get_text(strip=True) if title_el else ""

    return {"price": price, "title": title, "buybox_seller": buybox_seller, "buybox_price": price}

# ── MERCADO LIVRE ─────────────────────────────────────────────
_meli_seller_cache = {}

def get_meli_seller(seller_id):
    if seller_id in _meli_seller_cache:
        return _meli_seller_cache[seller_id]
    try:
        r = requests.get(
            f"https://api.mercadolibre.com/users/{seller_id}",
            headers=meli_headers(), timeout=10
        )
        if r.status_code == 200:
            name = r.json().get("nickname", "")
            _meli_seller_cache[seller_id] = name
            return name
    except: pass
    return None

def get_catalog_id(url):
    """Extrai catalog product ID da URL MELI."""
    m = re.search(r'/p/(MLB\d+)', url)
    return m.group(1) if m else None

def scrape_meli(url):
    global _meli_token

    if not _meli_token:
        return {"price": None, "error": "NO_MELI_TOKEN"}

    cat_id = get_catalog_id(url)
    if not cat_id:
        # Tenta extrair item direto da URL
        m = re.search(r'/(MLB\d+)(?:-|$|\?)', url)
        if m:
            cat_id = m.group(1)
        else:
            return {"price": None, "error": "NO_CATALOG_ID"}

    # Tenta /products/{id}/items — retorna o BuyBox winner e preço
    r = requests.get(
        f"https://api.mercadolibre.com/products/{cat_id}/items",
        headers=meli_headers(), timeout=15
    )

    # Token expirado — tenta renovar
    if r.status_code == 401:
        if refresh_meli_token():
            r = requests.get(
                f"https://api.mercadolibre.com/products/{cat_id}/items",
                headers=meli_headers(), timeout=15
            )
        else:
            return {"price": None, "error": "MELI_TOKEN_EXPIRED"}

    if r.status_code != 200:
        return {"price": None, "error": f"MELI_{r.status_code}"}

    results = r.json().get("results", [])
    if not results:
        return {"price": None, "error": "NO_RESULTS"}

    # Primeiro resultado = BuyBox winner (menor preço / melhor ranking)
    item = results[0]
    price     = item.get("price")
    seller_id = item.get("seller_id")
    seller_name = get_meli_seller(seller_id) if seller_id else None

    # Título do produto
    title = ""
    try:
        pr = requests.get(
            f"https://api.mercadolibre.com/products/{cat_id}",
            headers=meli_headers(), timeout=10
        )
        if pr.status_code == 200:
            title = pr.json().get("name", "")
    except: pass

    return {
        "price":          price,
        "title":          title,
        "buybox_seller":  seller_name,
        "buybox_price":   price,
        "original_price": item.get("original_price"),
        "item_id":        item.get("item_id"),
    }

# ── MAGALU ────────────────────────────────────────────────────
def scrape_magalu(url):
    price = None
    title = ""
    buybox_seller = None

    # Extrai seller da URL
    seller_m = re.search(r'seller_id=([^&/?]+)', url)
    if seller_m:
        buybox_seller = seller_m.group(1).replace("-", " ").title()

    if SCRAPERAPI_KEY:
        proxies = {
            "http":  f"http://scraperapi:{SCRAPERAPI_KEY}@proxy-server.scraperapi.com:8001",
            "https": f"http://scraperapi:{SCRAPERAPI_KEY}@proxy-server.scraperapi.com:8001",
        }
        try:
            r = requests.get(url, proxies=proxies, timeout=30, verify=False)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                # JSON-LD
                for script in soup.find_all("script", type="application/ld+json"):
                    try:
                        d = json.loads(script.string or "")
                        if isinstance(d, list): d = d[0]
                        if d.get("@type") == "Product":
                            title = d.get("name", "")
                            offers = d.get("offers", {})
                            if isinstance(offers, list): offers = offers[0]
                            price = float(offers.get("price", 0)) or None
                            if not buybox_seller:
                                s = offers.get("seller")
                                buybox_seller = (s.get("name") if isinstance(s, dict) else s) or buybox_seller
                            break
                    except: pass
                # CSS fallback
                if not price:
                    for sel in ["[data-testid='price-value']", ".price__value", "p[class*='price']"]:
                        el = soup.select_one(sel)
                        if el:
                            price = parse_brl(el.get_text())
                            if price: break
        except Exception as e:
            return {"price": None, "error": str(e)}
    else:
        return {"price": None, "error": "NO_SCRAPERAPI_KEY"}

    return {"price": price, "title": title, "buybox_seller": buybox_seller, "buybox_price": price}

# ── SITE PRÓPRIO ──────────────────────────────────────────────
def scrape_site(url):
    soup = fetch_page(url)
    if not soup:
        return {"price": None, "error": "FETCH_FAILED"}

    price = None
    title = ""
    buybox_seller = None

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(script.string or "")
            if isinstance(d, list): d = d[0]
            if d.get("@type") in ("Product", "Offer"):
                title = d.get("name", "")
                offers = d.get("offers", d)
                if isinstance(offers, list): offers = offers[0]
                price = float(offers.get("price", 0)) or None
                s = offers.get("seller")
                buybox_seller = (s.get("name") if isinstance(s, dict) else s)
                break
        except: pass

    if not price:
        for meta_name in ["product:price:amount", "og:price:amount"]:
            el = soup.find("meta", property=meta_name) or soup.find("meta", attrs={"name": meta_name})
            if el and el.get("content"):
                try: price = float(el["content"]); break
                except: pass

    if not price:
        for sel in [".price", ".product-price", "#product-price", "[data-price]", ".woocommerce-Price-amount"]:
            el = soup.select_one(sel)
            if el:
                price = parse_brl(el.get_text())
                if price: break

    if not title:
        h1 = soup.select_one("h1")
        title = h1.get_text(strip=True) if h1 else ""

    return {"price": price, "title": title, "buybox_seller": buybox_seller, "buybox_price": price}

# ── CORE ──────────────────────────────────────────────────────
def scrape_product(entry):
    url     = entry["url"]
    label   = entry.get("label", url[:60])
    channel = detect_channel(url)

    scraper = {
        "amazon":       scrape_amazon,
        "mercadolivre": scrape_meli,
        "magalu":       scrape_magalu,
        "site_proprio": scrape_site,
    }[channel]

    result = scraper(url)
    price  = result.get("price")
    bb     = result.get("buybox_seller")
    err    = result.get("error")

    status = f"✅ R${price:.2f}" if price else f"❌ {err or 'sem preço'}"
    bb_txt = f" | BuyBox: {bb}" if bb else ""
    print(f"  [{channel[:4].upper()}] {label[:40]:<40} {status}{bb_txt}")

    return {
        "id":            entry.get("id", url),
        "url":           url,
        "label":         label,
        "title":         result.get("title") or label,
        "channel":       channel,
        "group":         entry.get("group", ""),
        "size":          entry.get("size", ""),
        "type":          entry.get("type", ""),
        "model":         entry.get("model", ""),
        "price":         price,
        "buybox_seller": bb,
        "buybox_price":  result.get("buybox_price"),
        "original_price":result.get("original_price"),
        "has_error":     price is None,
        "error_detail":  err,
        "timestamp":     now_iso(),
    }

def run():
    print(f"\n{'='*60}")
    print(f"Price Tracker v3 — SellersFlow")
    print(f"Início: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"MELI:       {'✅' if MELI_TOKEN else '❌ sem token'}")
    print(f"ScraperAPI: {'✅' if SCRAPERAPI_KEY else '⚠️  sem chave (Magalu ignorado)'}")
    print(f"{'='*60}\n")

    links = json.loads(LINKS_FILE.read_text()) if LINKS_FILE.exists() else []
    if not links:
        print("❌ links.json não encontrado")
        sys.exit(1)

    # Load existing data to preserve history
    data = {"products": {}, "history": {}}
    if DATA_FILE.exists():
        try: data = json.loads(DATA_FILE.read_text())
        except: pass

    by_channel = {}
    for l in links:
        ch = detect_channel(l["url"])
        by_channel[ch] = by_channel.get(ch, 0) + 1
    print(f"Total: {len(links)} URLs | " + " | ".join(f"{k}: {v}" for k,v in by_channel.items()) + "\n")

    ok = errors = 0
    for entry in links:
        result = scrape_product(entry)
        pid = result["id"]

        data["products"][pid] = result

        if pid not in data["history"]:
            data["history"][pid] = []

        if result.get("price") is not None:
            data["history"][pid].append({
                "price":         result["price"],
                "buybox_seller": result.get("buybox_seller"),
                "buybox_price":  result.get("buybox_price"),
                "timestamp":     result["timestamp"],
            })
            data["history"][pid] = data["history"][pid][-500:]
            ok += 1
        else:
            errors += 1

        time.sleep(random.uniform(1.2, 2.8))

    data["last_updated"]   = now_iso()
    data["total_products"] = len(links)
    data["valid_readings"] = ok

    DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))

    print(f"\n{'='*60}")
    print(f"✅ {ok} leituras válidas | ❌ {errors} erros")
    print(f"Salvo em: {DATA_FILE}")
    print(f"{'='*60}")

if __name__ == "__main__":
    run()
