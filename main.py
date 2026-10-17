
import time
import uuid
import threading
from collections import defaultdict, deque
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(title="Q11 Orders API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

TOTAL_ORDERS = 54
RATE_LIMIT = 17
WINDOW_SECONDS = 10

# Fixed catalog: IDs 1 through 54.
catalog = [
    {"id": i, "item": f"Item {i}", "quantity": 1}
    for i in range(1, TOTAL_ORDERS + 1)
]

# In-memory state.
idempotency_keys = {}
created_orders = []
client_requests = defaultdict(deque)
lock = threading.Lock()


class OrderRequest(BaseModel):
    item: str = "Sample item"
    quantity: int = Field(default=1, ge=1)


def check_rate_limit(client_id: str):
    now = time.monotonic()

    with lock:
        requests = client_requests[client_id]

        while requests and now - requests[0] >= WINDOW_SECONDS:
            requests.popleft()

        if len(requests) >= RATE_LIMIT:
            retry_after = max(
                1, int(WINDOW_SECONDS - (now - requests[0]) + 0.999)
            )
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
                headers={"Retry-After": str(retry_after)},
            )

        requests.append(now)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # Rate-limit API requests using X-Client-Id.
    if request.url.path in ("/orders",):
        client_id = request.headers.get("X-Client-Id", "default-client")
        try:
            check_rate_limit(client_id)
        except HTTPException as exc:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=exc.headers,
            )

    return await call_next(request)


@app.get("/")
def root():
    return {"service": "Q11 Orders API", "total_orders": TOTAL_ORDERS}


@app.post("/orders", status_code=201)
def create_order(
    order: OrderRequest,
    idempotency_key: Optional[str] = Header(
        default=None, alias="Idempotency-Key"
    ),
):
    if not idempotency_key:
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key header is required",
        )

    with lock:
        if idempotency_key in idempotency_keys:
            return idempotency_keys[idempotency_key]

        new_order = {
            "id": str(uuid.uuid4()),
            "item": order.item,
            "quantity": order.quantity,
        }

        idempotency_keys[idempotency_key] = new_order
        created_orders.append(new_order)

        return new_order


@app.get("/orders")
def list_orders(
    limit: int = Query(default=10, ge=1, le=100),
    cursor: Optional[str] = None,
):
    # Cursor is the next catalog index, encoded as an opaque string.
    try:
        start = int(cursor) if cursor is not None else 0
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid cursor")

    if start < 0 or start > TOTAL_ORDERS:
        raise HTTPException(status_code=400, detail="Invalid cursor")

    end = min(start + limit, TOTAL_ORDERS)
    items = catalog[start:end]

    next_cursor = str(end) if end < TOTAL_ORDERS else None

    return {
        "items": items,
        "next_cursor": next_cursor,
    }


@app.get("/healthz")
def health():
    return {"status": "ok"}
