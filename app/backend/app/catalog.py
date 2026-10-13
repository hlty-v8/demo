"""Seed catalogue for the variant PDP.

One product with two option dimensions (colour x size) and seven SKUs, chosen so
that the shop is immediately useful for the frontend states the task requires:

* ``EMB-40`` is out of stock (available_qty == 0);
* ``sand`` x ``38`` is an impossible combination - no SKU exists for it, so the
  frontend has something real to disable;
* the remaining SKUs price the product differently per size, so switching a
  variant visibly changes the price.

Prices are stored in integer cents. Money is never represented as a float, in
the database, in the API or in the frontend.
"""

from __future__ import annotations

from typing import Any, Dict, List

PRODUCT: Dict[str, Any] = {
    "id": "aurora-trail-runner",
    "name": "Aurora Trail Runner",
    "description": (
        "A lightweight trail shoe built for long, loose descents: 6 mm drop, "
        "rock plate, and a grippy 4 mm lug pattern. True to size."
    ),
    "currency": "USD",
}

OPTION_DIMENSIONS: List[Dict[str, Any]] = [
    {
        "id": "color",
        "name": "Colour",
        "values": [
            {"id": "ember", "label": "Ember"},
            {"id": "moss", "label": "Moss"},
            {"id": "sand", "label": "Sand"},
        ],
    },
    {
        "id": "size",
        "name": "Size",
        "values": [
            {"id": "38", "label": "EU 38"},
            {"id": "40", "label": "EU 40"},
            {"id": "42", "label": "EU 42"},
            {"id": "44", "label": "EU 44"},
        ],
    },
]

# Colour used to render the per-SKU placeholder image (no binary assets needed).
COLOR_HEX = {"ember": "#c2410c", "moss": "#3f6212", "sand": "#a16207"}

# id, colour, size, price_cents, available_qty
SKUS: List[Dict[str, Any]] = [
    {"id": "EMB-38", "color": "ember", "size": "38", "price_cents": 12_900, "available_qty": 4},
    {"id": "EMB-40", "color": "ember", "size": "40", "price_cents": 12_900, "available_qty": 0},
    {"id": "EMB-42", "color": "ember", "size": "42", "price_cents": 12_900, "available_qty": 6},
    {"id": "MOS-40", "color": "moss", "size": "40", "price_cents": 12_900, "available_qty": 7},
    {"id": "MOS-42", "color": "moss", "size": "42", "price_cents": 13_200, "available_qty": 2},
    {"id": "SAND-42", "color": "sand", "size": "42", "price_cents": 13_500, "available_qty": 5},
    {"id": "SAND-44", "color": "sand", "size": "44", "price_cents": 13_500, "available_qty": 3},
]


def sku_image_url(sku_id: str) -> str:
    return f"/api/skus/{sku_id}/image"


def sku_image_svg(sku_id: str) -> str:
    """Deterministic placeholder artwork for a SKU."""
    sku = next((s for s in SKUS if s["id"] == sku_id), None)
    if sku is None:
        raise KeyError(sku_id)
    base = COLOR_HEX[sku["color"]]
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 600" role="img" aria-label="Aurora Trail Runner in {sku['color']}, size {sku['size']}">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{base}" stop-opacity="0.95"/>
      <stop offset="100%" stop-color="#0f172a" stop-opacity="0.9"/>
    </linearGradient>
  </defs>
  <rect width="600" height="600" fill="#f8fafc"/>
  <rect x="40" y="40" width="520" height="520" rx="32" fill="url(#g)"/>
  <path d="M110 380 C 170 300, 250 330, 300 290 C 350 250, 430 260, 490 320 L 490 400 C 430 430, 170 430, 110 400 Z"
        fill="#f8fafc" fill-opacity="0.92"/>
  <text x="300" y="500" text-anchor="middle" font-family="system-ui, sans-serif"
        font-size="42" fill="#f8fafc" font-weight="600">{sku['size']}</text>
</svg>
"""
