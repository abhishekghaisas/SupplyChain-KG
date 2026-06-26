# fmt: off
"""
fetch_digikey_catalog.py
─────────────────────────────────────────────────────────────────────────────
Fetch real parts and manufacturer data from the DigiKey Product Information
V4 API, then synthetically generate supplier ratings, lead times, and supply
relationships before writing everything to the knowledge graph.

Usage
─────
    # 1. Set credentials in .env (or export as env vars):
    #       DIGIKEY_CLIENT_ID=...
    #       DIGIKEY_CLIENT_SECRET=...
    #
    # 2. Run:
    #       python scripts/fetch_digikey_catalog.py
    #
    # 3. Optional flags:
    #       --dry-run          print JSON, don't write to Neo4j
    #       --limit 20         stop after N parts per category (default 10)
    #       --clear            wipe existing nodes before loading

What comes from DigiKey
───────────────────────
  Parts   : part number, name, description, category, specifications (from
            parametric data), RoHS/REACH compliance, HTS code, datasheet URL
  Manufacturers → mapped to Supplier nodes with real country-of-origin data
            derived from the manufacturer's known HQ country

What is synthesised
───────────────────
  Supplier ratings, on_time_delivery_rate, quality_rating, tier, established
  date, certifications (based on manufacturer country), contact info stubs,
  supply relationship pricing and lead times (derived from manufacturer lead
  weeks returned by DigiKey with ±20% noise).

API endpoints used
──────────────────
  POST /products/v4/search/keyword   — keyword search per category
  GET  /products/v4/search/{mpn}     — product details for a single MPN

Authentication: OAuth 2.0 two-legged (client credentials, no user login).
"""

import argparse
import json
import math
import os
import random
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dotenv import load_dotenv
import requests
from loguru import logger

# ── Add project root to path ──────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

# ── DigiKey API constants ─────────────────────────────────────────────────────

DK_TOKEN_URL = "https://api.digikey.com/v1/oauth2/token"
DK_KEYWORD_URL = "https://api.digikey.com/products/v4/search/keyword"
DK_DETAILS_URL = "https://api.digikey.com/products/v4/search/{mpn}/productdetails"

# Sandbox equivalents (set USE_SANDBOX=true in env for testing without hitting prod)
DK_SANDBOX_TOKEN_URL = "https://sandbox-api.digikey.com/v1/oauth2/token"
DK_SANDBOX_KEYWORD_URL = "https://sandbox-api.digikey.com/products/v4/search/keyword"
DK_SANDBOX_DETAILS_URL = "https://sandbox-api.digikey.com/products/v4/search/{mpn}/productdetails"

# ── Category search terms → our canonical category taxonomy ──────────────────
# Each entry: (search_keyword, our_category, criticality_default)
CATEGORY_QUERIES = [
    ("servo motor 24V",             "electromechanical", "HIGH"),
    ("stepper motor driver board",  "electronic",        "HIGH"),
    ("motor controller CAN bus",    "electronic",        "CRITICAL"),
    ("industrial power supply 24V", "electronic",        "HIGH"),
    ("mounting bracket steel",      "mechanical",        "MEDIUM"),
    ("power cable 24V industrial",  "electrical",        "LOW"),
    ("rotary encoder incremental",  "electromechanical", "MEDIUM"),
    ("pressure sensor 4-20mA",      "electronic",        "MEDIUM"),
    ("solenoid valve 24V",          "electromechanical", "MEDIUM"),
    ("din rail terminal block",     "electrical",        "LOW"),
    ("ARM cortex microcontroller",  "electronic",        "CRITICAL"),
    ("industrial ethernet switch",  "electronic",        "HIGH"),
    ("hydraulic pressure gauge",    "hydraulic",         "MEDIUM"),
    ("pneumatic cylinder",          "pneumatic",         "MEDIUM"),
    ("gear reducer",                "mechanical",        "MEDIUM"),
]

# ── Manufacturer country heuristics ──────────────────────────────────────────
# Maps manufacturer name fragments → country (used for supplier location)
MANUFACTURER_COUNTRY_MAP = {
    "panasonic": "Japan",
    "omron": "Japan",
    "keyence": "Japan",
    "yaskawa": "Japan",
    "mitsubishi": "Japan",
    "fanuc": "Japan",
    "murata": "Japan",
    "tdk": "Japan",
    "rohm": "Japan",
    "renesas": "Japan",
    "siemens": "Germany",
    "bosch": "Germany",
    "sick": "Germany",
    "festo": "Germany",
    "phoenix contact": "Germany",
    "weidmuller": "Germany",
    "pilz": "Germany",
    "heidenhain": "Germany",
    "ifm": "Germany",
    "beckhoff": "Germany",
    "schneider": "France",
    "legrand": "France",
    "crouzet": "France",
    "texas instruments": "USA",
    "microchip": "USA",
    "analog devices": "USA",
    "maxim": "USA",
    "on semiconductor": "USA",
    "vishay": "USA",
    "molex": "USA",
    "amphenol": "USA",
    "te connectivity": "USA",
    "parker": "USA",
    "emerson": "USA",
    "eaton": "USA",
    "honeywell": "USA",
    "rockwell": "USA",
    "allen-bradley": "USA",
    "st microelectronics": "Switzerland",
    "sensata": "Netherlands",
    "nxp": "Netherlands",
    "asml": "Netherlands",
    "infineon": "Germany",
    "continental": "Germany",
    "samsung": "South Korea",
    "lg": "South Korea",
    "hyundai": "South Korea",
    "delta": "Taiwan",
    "advantech": "Taiwan",
    "hiwin": "Taiwan",
    "airtac": "Taiwan",
    "mean well": "Taiwan",
    "abb": "Switzerland",
    "lenze": "Germany",
    "sew-eurodrive": "Germany",
    "nord": "Germany",
    "baumüller": "Germany",
    "rexroth": "Germany",
    "smc": "Japan",
    "cpc": "USA",
    "numatics": "USA",
    "norgren": "UK",
    "IMI": "UK",
}

# ── Certification profiles by country ────────────────────────────────────────
COUNTRY_CERTS = {
    "Germany":      ["CE", "ISO9001", "ISO14001", "IATF16949", "RoHS"],
    "USA":          ["UL", "ISO9001", "ITAR", "AS9100", "RoHS"],
    "Japan":        ["CE", "ISO9001", "ISO14001", "IATF16949", "RoHS"],
    "Taiwan":       ["CE", "ISO9001", "ISO14001", "RoHS"],
    "South Korea":  ["CE", "ISO9001", "ISO14001", "RoHS"],
    "France":       ["CE", "ISO9001", "ISO14001", "RoHS"],
    "Switzerland":  ["CE", "ISO9001", "ISO14001", "RoHS"],
    "UK":           ["CE", "ISO9001", "ISO14001", "RoHS"],
    "Netherlands":  ["CE", "ISO9001", "ISO14001", "RoHS"],
    "China":        ["CE", "ISO9001", "RoHS"],
    "Unknown":      ["ISO9001", "RoHS"],
}

# ── Supplier rating profiles by country (mean, std) ──────────────────────────
COUNTRY_RATING_PROFILE = {
    "Germany":      (4.5, 0.2),
    "Japan":        (4.6, 0.15),
    "USA":          (4.4, 0.25),
    "Switzerland":  (4.5, 0.2),
    "UK":           (4.2, 0.3),
    "France":       (4.1, 0.3),
    "Netherlands":  (4.3, 0.25),
    "Taiwan":       (4.1, 0.3),
    "South Korea":  (4.0, 0.3),
    "China":        (3.7, 0.4),
    "Unknown":      (3.8, 0.35),
}

# On-time delivery by country
COUNTRY_OTD_PROFILE = {
    "Germany":      (0.93, 0.03),
    "Japan":        (0.95, 0.02),
    "USA":          (0.92, 0.04),
    "Switzerland":  (0.93, 0.03),
    "UK":           (0.90, 0.05),
    "France":       (0.89, 0.05),
    "Netherlands":  (0.91, 0.04),
    "Taiwan":       (0.88, 0.05),
    "South Korea":  (0.87, 0.06),
    "China":        (0.84, 0.07),
    "Unknown":      (0.85, 0.06),
}


# ═════════════════════════════════════════════════════════════════════════════
# DigiKey API client
# ═════════════════════════════════════════════════════════════════════════════

class DigiKeyClient:
    """Thin wrapper around DigiKey Product Information V4 API."""

    def __init__(self, client_id: str, client_secret: str, sandbox: bool = False):
        self.client_id = client_id
        self.client_secret = client_secret
        self.sandbox = sandbox
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0

        self.token_url = DK_SANDBOX_TOKEN_URL if sandbox else DK_TOKEN_URL
        self.keyword_url = DK_SANDBOX_KEYWORD_URL if sandbox else DK_KEYWORD_URL
        self.details_url = DK_SANDBOX_DETAILS_URL if sandbox else DK_DETAILS_URL

        if sandbox:
            logger.info("DigiKey client using SANDBOX environment")

    # ── Auth ─────────────────────────────────────────────────────────────────

    def _get_token(self) -> str:
        """Return a valid access token, refreshing if within 60s of expiry."""
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token

        resp = requests.post(
            self.token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_expires_at = time.time() + data.get("expires_in", 600)
        logger.debug("DigiKey token refreshed")
        return self._token

    def _headers(self) -> Dict[str, str]:
        return {
            "authorization": f"Bearer {self._get_token()}",
            "X-DIGIKEY-Client-Id": self.client_id,
            "X-DIGIKEY-Locale-Site": "US",
            "X-DIGIKEY-Locale-Language": "en",
            "X-DIGIKEY-Locale-Currency": "USD",
            "content-type": "application/json",
            "accept": "application/json",
        }

    # ── Search ────────────────────────────────────────────────────────────────

    def keyword_search(
        self,
        keyword: str,
        limit: int = 10,
        in_stock_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Search by keyword and return up to `limit` products.

        Returns the raw 'Products' list from the DigiKey response.
        """
        # Minimal payload - DigiKey v4 rejects extra fields with 400
        payload = {
            "Keywords": keyword,
            "Limit": limit,
            "Offset": 0,
        }

        for attempt in range(3):
            try:
                resp = requests.post(
                    self.keyword_url,
                    headers=self._headers(),
                    data=json.dumps(payload),  # must be data= not json=
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                products = data.get("Products", []) or data.get("ExactMatches", [])
                logger.info(f"Keyword '{keyword}': {len(products)} results")
                return products
            except requests.HTTPError as e:
                if e.response.status_code == 429:
                    wait = 2 ** attempt * 5
                    logger.warning(f"Rate limited — waiting {wait}s")
                    time.sleep(wait)
                else:
                    try:
                        body = e.response.json()
                    except Exception:
                        body = e.response.text[:500]
                    logger.error(f"HTTP {e.response.status_code} on keyword search: {body}")
                    return []
            except Exception as e:
                logger.error(f"Keyword search failed (attempt {attempt+1}): {e}")
                time.sleep(2)

        return []

    def product_details(self, mpn: str) -> Optional[Dict[str, Any]]:
        """
        Fetch full product details for a single manufacturer part number.

        Returns the first matching product dict, or None.
        """
        url = self.details_url.format(mpn=requests.utils.quote(mpn, safe=""))
        try:
            resp = requests.get(url, headers=self._headers(), timeout=30)
            resp.raise_for_status()
            data = resp.json()
            products = data.get("ProductDetails", []) or data.get("Products", [])
            return products[0] if products else None
        except requests.HTTPError as e:
            logger.warning(f"ProductDetails {mpn}: HTTP {e.response.status_code}")
            return None
        except Exception as e:
            logger.warning(f"ProductDetails {mpn}: {e}")
            return None


# ═════════════════════════════════════════════════════════════════════════════
# Transformation: DigiKey product → our schema
# ═════════════════════════════════════════════════════════════════════════════

def _extract_specs(product: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract parametric specifications from a DigiKey product dict.

    Parametric data is in product["Parameters"] as a list of
    {"ParameterId": ..., "Parameter": "Voltage", "Value": "24V"}.
    We also pull in Classifications (RoHS, HTS) and ManufacturerLeadWeeks.
    """
    specs: Dict[str, Any] = {}

    # Parametric attributes
    for param in product.get("Parameters", []):
        key = param.get("Parameter", "").strip()
        value = param.get("Value", "").strip()
        if key and value and value not in ("-", "—", ""):
            # Normalise key to snake_case
            safe_key = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
            specs[safe_key] = value

    # Classifications
    classifications = product.get("Classifications") or {}
    if classifications.get("RohsStatus"):
        specs["rohs_status"] = classifications["RohsStatus"]
    if classifications.get("HtsusCode"):
        specs["hts_code"] = classifications["HtsusCode"]
    if classifications.get("ExportControlClassNumber"):
        specs["eccn"] = classifications["ExportControlClassNumber"]

    # Lead time hint
    lead_weeks = product.get("ManufacturerLeadWeeks")
    if lead_weeks:
        try:
            specs["manufacturer_lead_weeks"] = int(str(lead_weeks).split()[0])
        except (ValueError, IndexError):
            pass

    # Certifications inferred from RoHS
    certs = []
    if "rohs" in str(specs.get("rohs_status", "")).lower():
        certs.append("RoHS")
    if certs:
        specs["certifications"] = certs

    # Datasheet
    if product.get("DatasheetUrl"):
        specs["datasheet_url"] = product["DatasheetUrl"]

    return specs


def _normalise_category(dk_category_name: str, keyword_category: str) -> str:
    """
    Map a DigiKey category name to our canonical 9-value taxonomy.
    Falls back to keyword_category if no match.
    """
    name = dk_category_name.lower()

    mapping = [
        (["motor", "servo", "stepper", "actuator", "solenoid"],    "electromechanical"),
        (["sensor", "controller", "microcontroller", "processor",
          "driver", "amplifier", "ic", "integrated circuit",
          "switch", "relay", "encoder", "ethernet", "wireless",
          "fpga", "dsp", "memory", "power supply", "converter",
          "regulator", "oscillator", "filter"],                     "electronic"),
        (["cable", "wire", "connector", "terminal", "bus bar",
          "fuse", "breaker", "conduit"],                            "electrical"),
        (["bracket", "bearing", "gear", "shaft", "coupling",
          "fastener", "spring", "housing", "frame", "pulley"],     "mechanical"),
        (["hydraulic", "cylinder hydraulic", "pump hydraulic",
          "valve hydraulic", "accumulator"],                        "hydraulic"),
        (["pneumatic", "cylinder pneumatic", "valve pneumatic",
          "fitting pneumatic", "air"],                              "pneumatic"),
        (["software", "firmware", "license"],                       "software"),
        (["raw material", "metal", "plastic", "rubber", "foam"],   "raw_material"),
    ]

    for keywords, category in mapping:
        if any(kw in name for kw in keywords):
            return category

    return keyword_category


def _infer_criticality(category: str, specs: Dict[str, Any]) -> str:
    """
    Derive criticality from category and specifications.

    CRITICAL: control/processing components with no easy substitute
    HIGH:     primary functional components
    MEDIUM:   secondary functional components
    LOW:      consumables, cables, brackets
    """
    if category in ("software",):
        return "CRITICAL"
    if category == "electronic":
        name_lower = str(specs).lower()
        if any(k in name_lower for k in ("controller", "processor", "microcontroller", "fpga")):
            return "CRITICAL"
        return "HIGH"
    if category == "electromechanical":
        return "HIGH"
    if category in ("hydraulic", "pneumatic"):
        return "MEDIUM"
    if category == "mechanical":
        return "MEDIUM"
    if category in ("electrical",):
        return "LOW"
    return "MEDIUM"


def _slug(name: str) -> str:
    """Make a URL-safe slug from a manufacturer name."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _manufacturer_country(manufacturer_name: str) -> str:
    """Look up the country for a manufacturer by name fragment matching."""
    name_lower = manufacturer_name.lower()
    for fragment, country in MANUFACTURER_COUNTRY_MAP.items():
        if fragment in name_lower:
            return country
    return "Unknown"


def _synthetic_rating(country: str, seed: int) -> Tuple[float, float]:
    """
    Return (rating, on_time_delivery_rate) for a supplier in `country`.
    Uses a seeded RNG so results are deterministic across runs.
    """
    rng = random.Random(seed)
    r_mean, r_std = COUNTRY_RATING_PROFILE.get(country, (3.8, 0.35))
    otd_mean, otd_std = COUNTRY_OTD_PROFILE.get(country, (0.85, 0.06))
    rating = round(min(5.0, max(1.0, rng.gauss(r_mean, r_std))), 1)
    otd = round(min(0.99, max(0.60, rng.gauss(otd_mean, otd_std))), 2)
    return rating, otd


def _synthetic_lead_time(country: str, manufacturer_lead_weeks: Optional[int], rng: random.Random) -> int:
    """
    Return a lead time in days.
    Uses manufacturer_lead_weeks from DigiKey if available, else estimates by region.
    Adds ±20% noise.
    """
    if manufacturer_lead_weeks:
        base_days = manufacturer_lead_weeks * 7
    else:
        regional_base = {
            "Germany": 21, "USA": 18, "Japan": 28, "Taiwan": 35,
            "South Korea": 35, "China": 42, "France": 21,
            "Switzerland": 21, "UK": 21, "Netherlands": 21, "Unknown": 35,
        }
        base_days = regional_base.get(country, 35)

    noise = rng.uniform(0.80, 1.20)
    return max(7, round(base_days * noise))


def _synthetic_price(unit_price: Optional[float], rng: random.Random) -> float:
    """Apply ±15% noise to DigiKey unit price, or generate a placeholder."""
    if unit_price and unit_price > 0:
        return round(unit_price * rng.uniform(0.85, 1.15), 2)
    # Fallback: random price in a wide range
    return round(rng.uniform(5.0, 500.0), 2)


# ═════════════════════════════════════════════════════════════════════════════
# Transform raw DigiKey products into our graph schema
# ═════════════════════════════════════════════════════════════════════════════

def transform_products(
    raw_products: List[Dict[str, Any]],
    keyword_category: str,
    keyword_criticality: str,
) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """
    Transform a list of DigiKey products into:
      - parts:              list of part dicts matching create_part() signature
      - suppliers:          list of supplier dicts matching create_supplier()
      - supply_relationships: list of relationship dicts

    Deduplicates manufacturers → supplier nodes.
    """
    parts: List[Dict] = []
    suppliers_by_id: Dict[str, Dict] = {}  # supplier_id → supplier dict
    relationships: List[Dict] = []

    rng = random.Random(42)

    for product in raw_products:
        # ── Part ID ───────────────────────────────────────────────────────────
        mpn = product.get("ManufacturerProductNumber", "").strip()
        if not mpn:
            continue

        dk_number = ""
        for variation in product.get("ProductVariations", []):
            dk_number = variation.get("DigiKeyProductNumber", "")
            if dk_number:
                break

        part_id = f"DK-{re.sub(r'[^A-Z0-9-]', '', mpn.upper())[:20]}"

        # ── Specs ─────────────────────────────────────────────────────────────
        specs = _extract_specs(product)

        # ── Category & criticality ────────────────────────────────────────────
        dk_category = ""
        cat_obj = product.get("Category", {})
        if cat_obj:
            dk_category = cat_obj.get("Name", "") or cat_obj.get("Value", "")
        category = _normalise_category(dk_category, keyword_category)
        criticality = _infer_criticality(category, specs) or keyword_criticality

        # ── Unit price (use first break price from first variation) ───────────
        unit_price: Optional[float] = product.get("UnitPrice")
        if not unit_price:
            for variation in product.get("ProductVariations", []):
                pricing = variation.get("StandardPricing", [])
                if pricing:
                    unit_price = pricing[0].get("UnitPrice")
                    break

        # ── Description ───────────────────────────────────────────────────────
        desc_obj = product.get("Description", {})
        description = (
            desc_obj.get("DetailedDescription")
            or desc_obj.get("ProductDescription")
            or mpn
        )

        # ── Name ──────────────────────────────────────────────────────────────
        name = desc_obj.get("ProductDescription") or mpn

        # ── Manufacturer → Supplier ───────────────────────────────────────────
        mfr_obj = product.get("Manufacturer", {})
        mfr_name = mfr_obj.get("Name", "Unknown Manufacturer")
        mfr_id = mfr_obj.get("Id", 0)

        supplier_id = f"SUP-DK-{_slug(mfr_name)[:30].upper()}"
        country = _manufacturer_country(mfr_name)

        if supplier_id not in suppliers_by_id:
            rating, otd = _synthetic_rating(country, seed=mfr_id or hash(mfr_name) & 0xFFFF)
            certs = COUNTRY_CERTS.get(country, COUNTRY_CERTS["Unknown"]).copy()

            # Tier: well-known manufacturers → tier 1
            tier = 1 if rating >= 4.3 else 2

            # Established date: older = tier 1 bias
            year = rng.randint(2010, 2020) if tier == 1 else rng.randint(2015, 2022)
            month = rng.randint(1, 12)
            established = str(date(year, month, 1))

            suppliers_by_id[supplier_id] = {
                "id": supplier_id,
                "name": mfr_name,
                "location": country,
                "certifications": certs,
                "tier": tier,
                "status": "ACTIVE",
                "rating": rating,
                "on_time_delivery_rate": otd,
                "established_date": established,
                "contact_info": {
                    "email": f"procurement@{_slug(mfr_name)[:20]}.com",
                    "website": f"https://www.{_slug(mfr_name)[:20]}.com",
                },
                "source": "digikey_api",
            }

        # ── Supply relationship ───────────────────────────────────────────────
        lead_weeks = specs.get("manufacturer_lead_weeks")
        supplier = suppliers_by_id[supplier_id]
        lead_days = _synthetic_lead_time(country, lead_weeks, rng)
        price = _synthetic_price(unit_price, rng)

        relationships.append({
            "supplier_id": supplier_id,
            "part_id": part_id,
            "valid_from": str(date.today() - timedelta(days=rng.randint(30, 730))),
            "valid_to": None,
            "lead_time_days": lead_days,
            "price": price,
            "currency": "USD",
            "min_order_quantity": rng.choice([1, 5, 10, 25, 50, 100]),
            "on_time_delivery_rate": supplier["on_time_delivery_rate"],
            "quality_rating": supplier["rating"],
            "source": f"digikey_{mpn}",
            "confidence": 0.85,
        })

        # ── Part dict ─────────────────────────────────────────────────────────
        parts.append({
            "id": part_id,
            "name": name[:120],
            "description": description[:500],
            "category": category,
            "criticality": criticality,
            "unit_of_measure": "EA",
            "specifications": specs,
            "digikey_part_number": dk_number,
            "manufacturer_part_number": mpn,
            "source": "digikey_api",
        })

    return parts, list(suppliers_by_id.values()), relationships


# ═════════════════════════════════════════════════════════════════════════════
# Neo4j writer
# ═════════════════════════════════════════════════════════════════════════════

def write_to_graph(
    parts: List[Dict],
    suppliers: List[Dict],
    relationships: List[Dict],
    clear: bool = False,
) -> Dict[str, int]:
    """
    Write parts, suppliers, and supply relationships to Neo4j.
    Uses MERGE so re-runs are idempotent.
    Returns counts of nodes/relationships written.
    """
    from src.graph.neo4j_client import Neo4jClient

    counts = {"parts": 0, "suppliers": 0, "relationships": 0, "errors": 0}

    with Neo4jClient() as client:
        client.create_constraints()

        if clear:
            logger.warning("Clearing all existing data…")
            client.clear_all_data()

        # ── Suppliers (MERGE on id) ───────────────────────────────────────────
        for sup in suppliers:
            try:
                contact_json = json.dumps(sup.get("contact_info", {}))
                query = """
                MERGE (s:Supplier {id: $id})
                SET s.name               = $name,
                    s.location           = $location,
                    s.certifications     = $certifications,
                    s.tier               = $tier,
                    s.status             = $status,
                    s.rating             = $rating,
                    s.on_time_delivery_rate = $otd,
                    s.established_date   = $established,
                    s.contact_info_json  = $contact_json,
                    s.source             = $source,
                    s.updated_at         = datetime()
                """
                client.execute_write(query, {
                    "id":           sup["id"],
                    "name":         sup["name"],
                    "location":     sup["location"],
                    "certifications": sup["certifications"],
                    "tier":         sup["tier"],
                    "status":       sup["status"],
                    "rating":       sup["rating"],
                    "otd":          sup["on_time_delivery_rate"],
                    "established":  sup.get("established_date", "2020-01-01"),
                    "contact_json": contact_json,
                    "source":       sup.get("source", "digikey_api"),
                })
                counts["suppliers"] += 1
            except Exception as e:
                logger.error(f"Failed to write supplier {sup['id']}: {e}")
                counts["errors"] += 1

        logger.info(f"Wrote {counts['suppliers']} suppliers")

        # ── Parts (MERGE on id) ───────────────────────────────────────────────
        for part in parts:
            try:
                specs_json = json.dumps(part.get("specifications", {}))
                query = """
                MERGE (p:Part {id: $id})
                SET p.name               = $name,
                    p.description        = $description,
                    p.category           = $category,
                    p.criticality        = $criticality,
                    p.unit_of_measure    = $uom,
                    p.specifications_json = $specs_json,
                    p.manufacturer_part_number = $mpn,
                    p.digikey_part_number = $dkpn,
                    p.source             = $source,
                    p.updated_at         = datetime()
                """
                client.execute_write(query, {
                    "id":          part["id"],
                    "name":        part["name"],
                    "description": part["description"],
                    "category":    part["category"],
                    "criticality": part["criticality"],
                    "uom":         part.get("unit_of_measure", "EA"),
                    "specs_json":  specs_json,
                    "mpn":         part.get("manufacturer_part_number", ""),
                    "dkpn":        part.get("digikey_part_number", ""),
                    "source":      part.get("source", "digikey_api"),
                })
                counts["parts"] += 1
            except Exception as e:
                logger.error(f"Failed to write part {part['id']}: {e}")
                counts["errors"] += 1

        logger.info(f"Wrote {counts['parts']} parts")

        # ── Supply relationships (MERGE on supplier+part) ─────────────────────
        for rel in relationships:
            try:
                query = """
                MATCH (s:Supplier {id: $supplier_id})
                MATCH (p:Part      {id: $part_id})
                MERGE (s)-[r:SUPPLIES]->(p)
                SET r.valid_from           = date($valid_from),
                    r.valid_to             = CASE WHEN $valid_to IS NULL THEN NULL
                                             ELSE date($valid_to) END,
                    r.lead_time_days       = $lead_time_days,
                    r.price                = $price,
                    r.currency             = $currency,
                    r.min_order_quantity   = $moq,
                    r.on_time_delivery_rate = $otd,
                    r.quality_rating       = $quality_rating,
                    r.source               = $source,
                    r.confidence           = $confidence,
                    r.updated_at           = datetime()
                """
                client.execute_write(query, {
                    "supplier_id":    rel["supplier_id"],
                    "part_id":        rel["part_id"],
                    "valid_from":     rel["valid_from"],
                    "valid_to":       rel.get("valid_to"),
                    "lead_time_days": rel["lead_time_days"],
                    "price":          rel["price"],
                    "currency":       rel.get("currency", "USD"),
                    "moq":            rel.get("min_order_quantity", 1),
                    "otd":            rel.get("on_time_delivery_rate", 0.9),
                    "quality_rating": rel.get("quality_rating", 4.0),
                    "source":         rel.get("source", "digikey_api"),
                    "confidence":     rel.get("confidence", 0.85),
                })
                counts["relationships"] += 1
            except Exception as e:
                logger.error(f"Failed to write relationship {rel['supplier_id']}→{rel['part_id']}: {e}")
                counts["errors"] += 1

        logger.info(f"Wrote {counts['relationships']} supply relationships")

    return counts


# ═════════════════════════════════════════════════════════════════════════════
# Save JSON output
# ═════════════════════════════════════════════════════════════════════════════

def save_json_output(
    parts: List[Dict],
    suppliers: List[Dict],
    relationships: List[Dict],
    output_dir: Path,
) -> None:
    """Save the transformed data as JSON files (useful for inspection / replay)."""
    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "parts.json").write_text(json.dumps(parts, indent=2))
    (output_dir / "suppliers.json").write_text(json.dumps(suppliers, indent=2))
    (output_dir / "supply_relationships.json").write_text(json.dumps(relationships, indent=2))

    logger.info(f"JSON written to {output_dir}/")


# ═════════════════════════════════════════════════════════════════════════════
# Main
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch DigiKey catalog and load into Neo4j")
    parser.add_argument("--dry-run",  action="store_true", help="Print JSON only, skip Neo4j write")
    parser.add_argument("--limit",    type=int, default=10, help="Max parts per category (default 10)")
    parser.add_argument("--clear",    action="store_true", help="Clear existing data before loading")
    parser.add_argument("--sandbox",  action="store_true", help="Use DigiKey sandbox API")
    parser.add_argument("--output",   default="data/digikey", help="JSON output directory")
    parser.add_argument("--categories", nargs="*", help="Only run specific category keywords (by index)")
    args = parser.parse_args()

    # ── Credentials ──────────────────────────────────────────────────────────
    client_id = os.environ.get("DIGIKEY_CLIENT_ID", "").strip()
    client_secret = os.environ.get("DIGIKEY_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        logger.error(
            "Missing DIGIKEY_CLIENT_ID or DIGIKEY_CLIENT_SECRET. "
            "Set them in .env or export as environment variables.\n"
            "Get credentials at: https://developer.digikey.com/"
        )
        sys.exit(1)

    dk = DigiKeyClient(client_id, client_secret, sandbox=args.sandbox)

    # ── Fetch ─────────────────────────────────────────────────────────────────
    all_parts: List[Dict] = []
    all_suppliers: Dict[str, Dict] = {}
    all_relationships: List[Dict] = []

    queries = CATEGORY_QUERIES
    if args.categories:
        indices = [int(i) for i in args.categories]
        queries = [CATEGORY_QUERIES[i] for i in indices if i < len(CATEGORY_QUERIES)]

    for keyword, category, criticality in queries:
        logger.info(f"─── Fetching: '{keyword}' (category={category}) ───")
        raw = dk.keyword_search(keyword, limit=args.limit)

        if not raw:
            logger.warning(f"No results for '{keyword}', skipping")
            continue

        parts, suppliers, relationships = transform_products(raw, category, criticality)

        # Deduplicate suppliers across categories
        for sup in suppliers:
            if sup["id"] not in all_suppliers:
                all_suppliers[sup["id"]] = sup

        # Deduplicate parts by id
        existing_ids = {p["id"] for p in all_parts}
        for part in parts:
            if part["id"] not in existing_ids:
                all_parts.append(part)
                existing_ids.add(part["id"])

        all_relationships.extend(relationships)

        # Polite rate limiting
        time.sleep(0.5)

    supplier_list = list(all_suppliers.values())

    logger.info(
        f"\n{'='*60}\n"
        f"  Parts:         {len(all_parts)}\n"
        f"  Suppliers:     {len(supplier_list)}\n"
        f"  Relationships: {len(all_relationships)}\n"
        f"{'='*60}"
    )

    # ── Save JSON ─────────────────────────────────────────────────────────────
    save_json_output(all_parts, supplier_list, all_relationships, Path(args.output))

    # ── Write to graph ────────────────────────────────────────────────────────
    if args.dry_run:
        logger.info("Dry run — skipping Neo4j write. JSON saved to " + args.output)
        print(json.dumps({"parts": len(all_parts), "suppliers": len(supplier_list),
                          "relationships": len(all_relationships)}, indent=2))
        return

    counts = write_to_graph(all_parts, supplier_list, all_relationships, clear=args.clear)
    logger.success(
        f"Done! Written to Neo4j — "
        f"parts={counts['parts']}, suppliers={counts['suppliers']}, "
        f"relationships={counts['relationships']}, errors={counts['errors']}"
    )


if __name__ == "__main__":
    main()