
import time
import uuid
import threading
from collections import defaultdict, deque
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


# --------------------------------------------------
# Application configuration
# --------------------------------------------------

app = FastAPI(title="Q11 Orders API")

TOTAL_ORDERS = 54
RATE_LIMIT = 17
WINDOW_SECONDS = 10

# Fixed catalog with IDs 1 through 54.
catalog = [
    {"id": i, "item": f"Item {i}", "quantity": 1}
    for i in range(1, TOTAL_ORDERS + 1)
]

# In-memory storage.
idempotency_keys = {}
client_requests = defaultdict(deque)
lock = threading.Lock()


# --------------------------------------------------
# CORS
# --------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)


# --------------------------------------------------
# Request models
# --------------------------------------------------

class OrderRequest(BaseModel):
    item: str = "Sample item"
    quantity: int = Field(default=1, ge=1)


# --------------------------------------------------
# Rate limiting
# --------------------------------------------------

def get_retry_after(client_id: str) -> Optional[int]:
    """
    Allow 17 requests per client in a rolling 10-second window.
    Return None if allowed; otherwise return Retry-After seconds.
    """
    now = time.monotonic()

    with lock:
        requests = client_requests[client_id]

        # Remove timestamps outside the rolling window.
        while requests and now - requests[0] >= WINDOW_SECONDS:
            requests.popleft()

        if len(requests) >= RATE_LIMIT:
            wait_seconds = WINDOW_SECONDS - (now - requests[0])
            return max(1, int(wait_seconds + 0.999))

        requests.append(now)
        return None


@app.middleware("http")
async def rate_limit_middleware(request, call_next):
    # Apply the limit to both GET and POST /orders.
    if request.url.path == "/orders" and request.method in ("GET", "POST"):
        client_id = request.headers.get("X-Client-Id", "default-client")

        retry_after = get_retry_after(client_id)

        if retry_after is not None:
            response = JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={
                    "Retry-After": str(retry_after),
                },
            )
            return response

    return await call_next(request)


# --------------------------------------------------
# Root and health endpoints
# --------------------------------------------------

@app.get("/")
def root():
    return {
        "service": "Q11 Orders API",
        "total_orders": TOTAL_ORDERS,
    }


@app.get("/healthz")
def health():
    return {"status": "ok"}


# --------------------------------------------------
# Create orders with idempotency
# --------------------------------------------------

@app.post("/orders", status_code=201)
def create_order(
    order: OrderRequest,
    idempotency_key: Optional[str] = Header(
        default=None,
        alias="Idempotency-Key",
    ),
):
    if not idempotency_key:
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key header is required",
        )

    with lock:
        # Return the original order for a repeated key.
        if idempotency_key in idempotency_keys:
            return idempotency_keys[idempotency_key]

        new_order = {
            "id": str(uuid.uuid4()),
            "item": order.item,
            "quantity": order.quantity,
        }

        idempotency_keys[idempotency_key] = new_order

        return new_order


# --------------------------------------------------
# Cursor-based pagination
# --------------------------------------------------

@app.get("/orders")
def list_orders(
    limit: int = Query(default=10, ge=1, le=100),
    cursor: Optional[str] = None,
):
    # The cursor is the index of the next catalog item.
    if cursor is None or cursor == "":
        start = 0
    else:
        try:
            start = int(cursor)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail="Invalid cursor",
            )

    if start < 0 or start >= TOTAL_ORDERS:
        if start == TOTAL_ORDERS:
            return {"items": [], "next_cursor": None}

        raise HTTPException(
            status_code=400,
            detail="Invalid cursor",
        )

    end = min(start + limit, TOTAL_ORDERS)
    items = catalog[start:end]

    next_cursor = str(end) if end < TOTAL_ORDERS else None

    return {
        "items": items,
        "next_cursor": next_cursor,
    }
