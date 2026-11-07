"""Sample OAuth 2.0 client-credentials target for the load-testing toolkit.

POST /token issues short-lived bearer tokens; /customers needs one. GET /stats shows how many
tokens were issued and how many requests were rejected, which is how you confirm the toolkit
refreshes tokens before they expire. Demo only: credentials are public and tokens live in memory.
"""
import base64
import os
import secrets
import threading
import time
from typing import Optional

from fastapi import FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import JSONResponse

CLIENT_ID = os.environ.get("OAUTH_STUB_CLIENT_ID", "loadtest-client")
CLIENT_SECRET = os.environ.get("OAUTH_STUB_CLIENT_SECRET", "loadtest-secret")
TOKEN_TTL_SEC = int(os.environ.get("OAUTH_STUB_TOKEN_TTL_SEC", "30"))
REQUIRED_SCOPE = os.environ.get("OAUTH_STUB_SCOPE", "")

app = FastAPI(title="Boomi Load Testing: OAuth Sample Target")
_lock = threading.Lock()
_tokens: dict[str, float] = {}
_stats = {"tokens_issued": 0, "token_requests_rejected": 0, "api_ok": 0, "api_rejected": 0}

CUSTOMERS = [{"customer_id": f"CUST-{i:06d}", "name": f"Customer {i}"} for i in range(1, 51)]


def _basic_credentials(authorization: Optional[str]):
    if authorization and authorization.lower().startswith("basic "):
        try:
            raw = base64.b64decode(authorization.split(" ", 1)[1]).decode()
            cid, _, secret = raw.partition(":")
            return cid, secret
        except Exception:
            return None, None
    return None, None


@app.post("/token")
async def token(grant_type: str = Form(""), scope: str = Form(""), client_id: Optional[str] = Form(None),
                client_secret: Optional[str] = Form(None), authorization: Optional[str] = Header(None)):
    cid, secret = _basic_credentials(authorization)
    if cid is None:
        cid, secret = client_id, client_secret
    if grant_type != "client_credentials":
        with _lock:
            _stats["token_requests_rejected"] += 1
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
    if not (secrets.compare_digest(cid or "", CLIENT_ID) and secrets.compare_digest(secret or "", CLIENT_SECRET)):
        with _lock:
            _stats["token_requests_rejected"] += 1
        return JSONResponse({"error": "invalid_client", "error_description": "Client authentication failed"}, status_code=401)
    access = secrets.token_urlsafe(24)
    with _lock:
        _tokens[access] = time.time() + TOKEN_TTL_SEC
        _stats["tokens_issued"] += 1
    return {"access_token": access, "token_type": "Bearer", "expires_in": TOKEN_TTL_SEC, "scope": scope or REQUIRED_SCOPE}


def _require_token(authorization: Optional[str]):
    token_value = authorization.split(" ", 1)[1] if authorization and authorization.lower().startswith("bearer ") else ""
    with _lock:
        expires = _tokens.get(token_value)
        ok = expires is not None and expires > time.time()
        _stats["api_ok" if ok else "api_rejected"] += 1
    if not ok:
        raise HTTPException(status_code=401, detail="invalid_token", headers={"WWW-Authenticate": "Bearer"})


@app.get("/customers")
def customers(authorization: Optional[str] = Header(None)):
    _require_token(authorization)
    return CUSTOMERS[:10]


@app.get("/customers/{customer_id}")
def customer(customer_id: str, authorization: Optional[str] = Header(None)):
    _require_token(authorization)
    for c in CUSTOMERS:
        if c["customer_id"] == customer_id:
            return c
    raise HTTPException(status_code=404, detail="not found")


@app.get("/stats")
def stats():
    with _lock:
        return {**_stats, "token_ttl_sec": TOKEN_TTL_SEC}


@app.get("/ping")
def ping():
    return {"status": "ok"}
