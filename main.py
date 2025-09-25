"""
FastAPI backend for Taaza Chatbot
- Serves static frontend from ./static
- Exposes endpoints:
  GET  /categories                 -> proxy to CATEGORY_API_URL
  GET  /items/{category}           -> proxy to ITEMS_API_BASE/{category}
  POST /session/create             -> create session & validate user (name, mobile, address)
  POST /cart/add                   -> add item to session cart
  POST /cart/remove                -> remove item or reduce qty
  GET  /cart/view                  -> view cart + user info (session_id query)
  POST /checkout                   -> finalize order and forward to BILL_API_URL
  POST /session/reset              -> clear session (logout)
"""
import os
import time
import threading
import json
from typing import Dict, Any, List, Optional

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

# Config from .env
CATEGORY_API_URL = os.getenv("CATEGORY_API_URL", "https://pos-backend-nine-pied.vercel.app/api/categories/list")
ITEMS_API_BASE = os.getenv("ITEMS_API_BASE", "https://pos-backend-nine-pied.vercel.app/api/item-details/category")
BILL_API_URL = os.getenv("BILL_API_URL", "https://pos-backend-nine-pied.vercel.app/api/bookings")
BILL_API_AUTH = os.getenv("BILL_API_AUTH")  # optional: set Authorization token here
AUTO_LOGOUT_SECONDS = int(os.getenv("AUTO_LOGOUT_SECONDS", "1800"))  # default 30 minutes

app = FastAPI(title="Taaza Chatbot API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

# serve static frontend
app.mount("/static", StaticFiles(directory="static"), name="static")


# -------------------------
# In-memory session store
# -------------------------
# sessions: session_id -> dict
# session structure:
# {
#   "created_at": timestamp,
#   "user": {"name": str, "mobile": "+92300....", "address": str},
#   "cart": [ {"name": str, "price": float, "qty": int, "subtotal": float}, ... ],
#   "categories": [...],  # optional cache
#   "selected_cat": str
# }
sessions: Dict[str, Dict[str, Any]] = {}


def reset_session_later(session_id: str, delay: int = AUTO_LOGOUT_SECONDS):
    """Auto-logout helper — removes session after `delay` seconds."""
    def _worker():
        time.sleep(delay)
        sessions.pop(session_id, None)
    t = threading.Thread(target=_worker, daemon=True)
    t.start()


# -------------------------
# Helper functions (kept logic similar to your Streamlit version)
# -------------------------
def validate_mobile(mobile: str) -> bool:
    """
    Validate Pakistan mobile number rules:
    - Only digits
    - Starts with '3'
    - Exactly 10 digits (e.g., 3001234567)
    """
    if not mobile or not mobile.isdigit():
        return False
    if not mobile.startswith("3"):
        return False
    if len(mobile) != 10:
        return False
    return True


def add_to_cart_state(state: Dict[str, Any], item_name: str, price: float, qty: int = 1):
    """Add item to the session cart (update qty if already exists)."""
    if "cart" not in state:
        state["cart"] = []
    for it in state["cart"]:
        if it["name"] == item_name:
            it["qty"] += qty
            it["subtotal"] = it["qty"] * it["price"]
            return
    state["cart"].append({"name": item_name, "price": float(price), "qty": int(qty), "subtotal": float(price) * int(qty)})


def remove_from_cart_state(state: Dict[str, Any], item_name: str, qty: int = 1) -> Dict[str, Any]:
    """Remove item or reduce quantity. Returns operation result dict."""
    cart = state.get("cart", [])
    for i, it in enumerate(cart):
        if it["name"].lower() == item_name.lower():
            if it["qty"] <= qty:
                cart.pop(i)
                return {"success": True, "message": f"Removed {item_name} from cart."}
            else:
                it["qty"] -= qty
                it["subtotal"] = it["qty"] * it["price"]
                return {"success": True, "message": f"Reduced {item_name} quantity by {qty}."}
    return {"success": False, "message": f"Item '{item_name}' not present in cart."}


def compute_cart_summary(cart: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return lines and total for cart."""
    lines = []
    total = 0
    for it in cart:
        lines.append({"name": it["name"], "qty": it["qty"], "rate": it["price"], "amount": it["subtotal"]})
        total += it["subtotal"]
    return {"lines": lines, "total": total}


# -------------------------
# Pydantic models for requests
# -------------------------
class SessionCreateRequest(BaseModel):
    name: str
    mobile: str  # expect 10-digit without country code e.g. 3001234567
    address: str
    country_code: Optional[str] = "+92"


class CartAddRequest(BaseModel):
    session_id: str
    itemName: str
    price: float
    qty: int = 1


class CartRemoveRequest(BaseModel):
    session_id: str
    itemName: str
    qty: int = 1


class CheckoutRequest(BaseModel):
    session_id: str
    paymentMethod: str  # "Cash on Delivery" or "Online Transfer"


# -------------------------
# Routes
# -------------------------
@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.get("/categories")
def get_categories():
    """Proxy to CATEGORY_API_URL and return JSON list."""
    try:
        r = requests.get(CATEGORY_API_URL, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch categories: {str(e)}")


@app.get("/items/{category}")
def get_items(category: str):
    """Proxy to ITEMS_API_BASE/{category}"""
    try:
        r = requests.get(f"{ITEMS_API_BASE}/{category}", timeout=15)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        # forward 404 as 404
        status = getattr(e.response, "status_code", 502)
        raise HTTPException(status_code=status, detail=f"Items API error: {e.response.text if e.response is not None else str(e)}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch items: {str(e)}")


@app.post("/session/create")
def session_create(req: SessionCreateRequest):
    """
    Create a new session for the user after validating mobile.
    Returns session_id to be stored on frontend.
    """
    name = req.name.strip()
    mobile = req.mobile.strip()
    address = req.address.strip()
    country_code = req.country_code or "+92"

    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty.")
    if not validate_mobile(mobile):
        raise HTTPException(status_code=400, detail="Mobile must be 10 digits, start with 3 and contain only digits (e.g., 3001234567).")
    if not address or len(address) < 3:
        raise HTTPException(status_code=400, detail="Address cannot be empty or too short.")

    # create session id (simple)
    session_id = f"sess_{int(time.time()*1000)}"
    sessions[session_id] = {
        "created_at": time.time(),
        "user": {"name": name, "mobile": f"{country_code}{mobile}", "address": address},
        "cart": [],
        "categories": None,
        "selected_cat": None
    }

    # schedule auto logout
    reset_session_later(session_id)
    return {"session_id": session_id, "user": sessions[session_id]["user"]}


@app.post("/cart/add")
def cart_add(req: CartAddRequest):
    sid = req.session_id
    if sid not in sessions:
        raise HTTPException(status_code=404, detail="Session not found.")
    # add to cart
    add_to_cart_state(sessions[sid], req.itemName, req.price, req.qty)
    return {"success": True, "cart": sessions[sid]["cart"], "summary": compute_cart_summary(sessions[sid]["cart"])}


@app.post("/cart/remove")
def cart_remove(req: CartRemoveRequest):
    sid = req.session_id
    if sid not in sessions:
        raise HTTPException(status_code=404, detail="Session not found.")
    res = remove_from_cart_state(sessions[sid], req.itemName, req.qty)
    return {"result": res, "cart": sessions[sid]["cart"], "summary": compute_cart_summary(sessions[sid]["cart"])}


@app.get("/cart/view")
def cart_view(session_id: str):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found.")
    state = sessions[session_id]
    summary = compute_cart_summary(state.get("cart", []))
    return {"user": state.get("user", {}), "cart": state.get("cart", []), "summary": summary}


@app.post("/checkout")
def checkout(req: CheckoutRequest):
    sid = req.session_id
    if sid not in sessions:
        raise HTTPException(status_code=404, detail="Session not found.")
    state = sessions[sid]
    cart = state.get("cart", [])
    if not cart:
        raise HTTPException(status_code=400, detail="Cart is empty.")

    # Prepare data per required payload
    items_payload = []
    total = 0
    for it in cart:
        items_payload.append({
            "itemName": it["name"],
            "qty": it["qty"],
            "rate": it["price"],
            "amount": it["subtotal"]
        })
        total += it["subtotal"]

    payload = {
        "customerName": state["user"].get("name", "Guest"),
        "mobileNo": state["user"].get("mobile", ""),
        "address": state["user"].get("address", ""),
        "items": items_payload,
        "total": total,
        "paymentMethod": req.paymentMethod
    }

    headers = {"Content-Type": "application/json"}
    if BILL_API_AUTH:
        headers["Authorization"] = f"Bearer {BILL_API_AUTH}"

    try:
        resp = requests.post(BILL_API_URL, json=payload, headers=headers, timeout=15)
        # best effort: swallow errors? we'll forward status to client
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to forward to billing API: {str(e)}")

    # If success, clear cart and schedule session reset
    if resp.status_code in (200, 201):
        state["cart"] = []
        reset_session_later(sid, delay=30)  # short delay then remove
        return {"success": True, "message": "Order placed", "bill_api_status": resp.status_code, "payload_sent": payload}
    else:
        # forward response body (if JSON) or text
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        raise HTTPException(status_code=502, detail={"billing_status": resp.status_code, "billing_response": body})


@app.post("/session/reset")
def session_reset(request: Request):
    body = {}
    try:
        body = request.json()
    except Exception:
        pass
    session_id = body.get("session_id") if isinstance(body, dict) else None
    if session_id and session_id in sessions:
        sessions.pop(session_id, None)
        return {"reset": True}
    return {"reset": False}


# Note: this is a minimal production-ish example. For production you should use
# persistent store for sessions, proper authentication, rate-limiting, retries and more.
