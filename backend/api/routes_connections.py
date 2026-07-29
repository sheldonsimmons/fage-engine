"""Persistent platform connections, OAuth handshakes, and metadata discovery."""

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import uuid
from base64 import b64encode
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote, urlencode, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import IntegrationConnection, TrialAccount


router = APIRouter()
logger = logging.getLogger(__name__)
SUPPORTED_PLATFORMS = {"salesforce", "servicenow", "hubspot"}
SALESFORCE_API_VERSION = os.getenv("SALESFORCE_API_VERSION", "v65.0")
SERVICENOW_DEFAULT_TABLES = {
    "incident",
    "problem",
    "change_request",
    "sc_request",
    "sn_customerservice_case",
    "pm_project",
    "task",
}
SERVICENOW_INHERITED_TASK_FIELDS = [
    {"name": "sys_id", "label": "Sys ID", "type": "GUID", "readable": True, "writable": False, "reference_to": []},
    {"name": "number", "label": "Number", "type": "string", "readable": True, "writable": False, "reference_to": []},
    {"name": "short_description", "label": "Short description", "type": "string", "readable": True, "writable": True, "reference_to": []},
    {"name": "description", "label": "Description", "type": "html", "readable": True, "writable": True, "reference_to": []},
    {"name": "assigned_to", "label": "Assigned to", "type": "reference", "readable": True, "writable": True, "reference_to": ["sys_user"]},
    {"name": "company", "label": "Company", "type": "reference", "readable": True, "writable": True, "reference_to": ["core_company"]},
    {"name": "state", "label": "State", "type": "integer", "readable": True, "writable": True, "reference_to": []},
]


class ConnectionCreate(BaseModel):
    workspace_id: str = Field(default="default", min_length=1, max_length=160)
    platform: str
    display_name: str = Field(min_length=1, max_length=200)
    auth_base_url: Optional[str] = None


class ObjectDiscoveryRequest(BaseModel):
    object_name: str = Field(min_length=1, max_length=160)


class MappingUpdate(BaseModel):
    selected_object: str = Field(min_length=1, max_length=160)
    mapping: dict


def _new_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("utf-8")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _new_salesforce_workspace(identity: dict, db: Session) -> TrialAccount:
    """Resolve the CostPilot workspace for a verified Salesforce administrator."""
    email = str(identity.get("email") or identity.get("username") or "").strip().lower()
    if email:
        existing = db.query(TrialAccount).filter(TrialAccount.email == email).first()
        if existing:
            if not existing.secret_key:
                existing.secret_key = "sk-cp-" + secrets.token_urlsafe(32)
            existing.platform = "salesforce"
            existing.is_active = True
            db.flush()
            return existing

    org_id = str(identity.get("organization_id") or "salesforce").strip()
    fallback_email = f"salesforce-{org_id.lower()}@connected.costpilot.local"
    existing = db.query(TrialAccount).filter(TrialAccount.email == fallback_email).first()
    if existing:
        if not existing.secret_key:
            existing.secret_key = "sk-cp-" + secrets.token_urlsafe(32)
        existing.is_active = True
        db.flush()
        return existing

    workspace_id = uuid.uuid4().hex[:16].upper()
    account = TrialAccount(
        email=email or fallback_email,
        name=str(identity.get("display_name") or identity.get("username") or "Salesforce Administrator"),
        company=str(identity.get("organization_id") or "Salesforce"),
        api_key_enc=b64encode(b"").decode(),
        provider="openai",
        workspace_id=workspace_id,
        secret_key="sk-cp-" + secrets.token_urlsafe(32),
        platform="salesforce",
        setup_complete=False,
        trial_start=datetime.utcnow(),
        trial_end=datetime.utcnow() + timedelta(days=30),
        plan="trial",
        is_active=True,
        trial_call_cap=500,
        trial_spend_cap_usd=10.0,
    )
    db.add(account)
    db.flush()
    return account


async def _populate_salesforce_costpilot_credential(
    *,
    instance_url: str,
    access_token: str,
    secret_key: str,
) -> tuple[bool, str]:
    """Populate the packaged encrypted principal through Salesforce Connect REST."""
    endpoint = (
        f"{instance_url.rstrip('/')}/services/data/{SALESFORCE_API_VERSION}"
        "/named-credentials/credential"
    )
    payload = {
        "authenticationProtocol": "Custom",
        "credentials": {
            "CostPilotKey": {
                "value": secret_key,
                "encrypted": True,
            }
        },
        "externalCredential": "CostPilotExternal",
        "principalName": "CostPilotKey",
        "principalType": "NamedPrincipal",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if response.status_code in (200, 201, 204):
        return True, ""
    # Never return the request body because it contains the workspace secret.
    return False, f"Salesforce credential provisioning returned HTTP {response.status_code}."


def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="Encrypted connection storage is unavailable") from exc
    secret = (
        os.getenv("CONNECTION_ENCRYPTION_KEY")
        or os.getenv("SECRET_KEY")
        or os.getenv("COSTPILOT_SECRET_KEY")
    )
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="Connection encryption is not configured. Set CONNECTION_ENCRYPTION_KEY.",
        )
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def _encrypt(value: Optional[str]) -> Optional[str]:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii") if value else None


def _decrypt(value: Optional[str]) -> Optional[str]:
    return _fernet().decrypt(value.encode("ascii")).decode("utf-8") if value else None


def _public_connection(item: IntegrationConnection) -> dict:
    mapping = json.loads(item.mapping_json) if item.mapping_json else None
    if isinstance(mapping, dict):
        mapping = {
            key: value
            for key, value in mapping.items()
            if not str(key).startswith("_")
        }
    return {
        "id": item.id,
        "workspace_id": item.workspace_id,
        "platform": item.platform,
        "display_name": item.display_name,
        "status": item.status,
        "configured": bool(item.access_token_encrypted),
        "instance_url": item.instance_url,
        "external_tenant_id": item.external_tenant_id,
        "selected_object": item.selected_object,
        "mapping": mapping,
        "last_tested_at": item.last_tested_at,
        "last_success_at": item.last_success_at,
        "last_error": item.last_error,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _get_connection(db: Session, connection_id: int) -> IntegrationConnection:
    item = db.query(IntegrationConnection).filter(IntegrationConnection.id == connection_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Connection was not found")
    return item


def _salesforce_auth_base(value: Optional[str]) -> str:
    base = (value or os.getenv("SALESFORCE_AUTH_BASE_URL") or "https://login.salesforce.com").rstrip("/")
    parsed = urlparse(base)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        hostname == "salesforce.com"
        or hostname.endswith(".salesforce.com")
        or hostname.endswith(".my.salesforce.com")
    ):
        raise HTTPException(status_code=400, detail="Salesforce authorization URL must be an HTTPS Salesforce domain")
    return base


def _servicenow_auth_base(value: Optional[str]) -> str:
    base = (value or "").strip().rstrip("/")
    parsed = urlparse(base)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        hostname == "service-now.com" or hostname.endswith(".service-now.com")
    ):
        raise HTTPException(
            status_code=400,
            detail="ServiceNow instance URL must be an HTTPS service-now.com domain",
        )
    return base


def recommend_business_mapping(fields: list[dict]) -> dict:
    """Recommend semantic mappings from platform field metadata without reading records."""
    targets = {
        "work_id": ["id", "sys_id", "record_id", "project_id", "matter_id"],
        "work_name": ["name", "title", "subject", "short_description"],
        "owner": ["ownerid", "owner_id", "assigned_to", "hubspot_owner_id", "project_manager"],
        "customer": ["accountid", "account_id", "company", "customer", "client"],
        "status": ["status", "state", "stage", "pipeline_stage"],
        "content": ["description", "body", "details", "notes", "content"],
    }
    normalized = []
    for field in fields:
        name = str(field.get("name") or field.get("api_name") or "")
        label = str(field.get("label") or name)
        searchable = f"{name} {label}".lower().replace("__c", "").replace("_", " ")
        normalized.append((field, name, label, searchable))

    recommendations = {}
    for target, candidates in targets.items():
        scored = []
        for field, name, label, searchable in normalized:
            score = 0
            compact = searchable.replace(" ", "")
            for candidate in candidates:
                candidate_words = candidate.replace("_", " ")
                if name.lower() == candidate:
                    score = max(score, 100)
                elif compact == candidate.replace("_", ""):
                    score = max(score, 95)
                elif candidate_words in searchable:
                    score = max(score, 82)
            if score:
                scored.append((score, name, label, field.get("type") or field.get("dataType")))
        scored.sort(reverse=True)
        if scored:
            score, name, label, data_type = scored[0]
            recommendations[target] = {
                "field": name,
                "label": label,
                "data_type": data_type,
                "confidence": "high" if score >= 90 else "medium",
                "score": score,
            }
    return recommendations


def recommend_child_relationships(relationships: list[dict]) -> list[dict]:
    """Return useful direct children from a platform parent-object description."""
    suggestions = []
    ignored_suffixes = (
        "ChangeEvent",
        "Feed",
        "History",
        "Share",
        "Tag",
    )
    ignored_objects = {
        "ActivityHistory",
        "AttachedContentDocument",
        "CombinedAttachment",
        "ContentDocumentLink",
        "Event",
        "Note",
        "OpenActivity",
        "ProcessInstance",
        "Task",
    }
    for relationship in relationships:
        child_object = str(relationship.get("childSObject") or "").strip()
        parent_field = str(relationship.get("field") or "").strip()
        relationship_name = str(relationship.get("relationshipName") or "").strip()
        if (
            not child_object
            or not parent_field
            or child_object in ignored_objects
            or child_object.endswith(ignored_suffixes)
        ):
            continue
        is_custom = child_object.endswith("__c")
        common_child = child_object in {
            "Account",
            "Case",
            "Contact",
            "Contract",
            "Lead",
            "Opportunity",
            "Order",
            "Quote",
            "WorkOrder",
        }
        score = 100 if is_custom else 90 if common_child else 60
        suggestions.append({
            "object": child_object,
            "label": child_object.removesuffix("__c").replace("_", " "),
            "parent_field": parent_field,
            "relationship_name": relationship_name or None,
            "cascade_delete": bool(relationship.get("cascadeDelete")),
            "confidence": "high" if score >= 90 else "medium",
            "score": score,
            "recommended_behavior": "track_and_rollup",
        })
    suggestions.sort(key=lambda item: (-item["score"], item["label"].lower(), item["object"]))
    return suggestions


@router.get("/salesforce/package/start")
def start_salesforce_package_setup(
    org_id: str = Query(min_length=15, max_length=18),
    instance_url: str = Query(min_length=1, max_length=300),
    db: Session = Depends(get_db),
):
    """Start the post-install connection without asking the customer for a key."""
    auth_base = _salesforce_auth_base(instance_url)
    client_id = os.getenv("SALESFORCE_CLIENT_ID")
    redirect_uri = os.getenv(
        "SALESFORCE_REDIRECT_URI",
        "https://fage-engine-21cb49fe4806.herokuapp.com/api/integrations/connections/oauth/salesforce/callback",
    )
    if not client_id:
        return RedirectResponse(
            url="/salesforce-setup.html?status=error&reason=oauth_not_configured"
        )

    state = secrets.token_urlsafe(32)
    pkce_verifier, pkce_challenge = _new_pkce_pair()
    item = IntegrationConnection(
        workspace_id=f"pending-{secrets.token_hex(8)}",
        platform="salesforce",
        display_name=f"Salesforce package setup {org_id}",
        status="authorizing",
        auth_base_url=auth_base,
        oauth_state=state,
        mapping_json=json.dumps({
            "package_setup": True,
            "requested_org_id": org_id,
            "_oauth_pkce_verifier": pkce_verifier,
        }),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    query = urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "api refresh_token",
        "state": state,
        "prompt": "login",
        "code_challenge": pkce_challenge,
        "code_challenge_method": "S256",
    })
    return RedirectResponse(url=f"{auth_base}/services/oauth2/authorize?{query}")


@router.get("/salesforce/package/status")
async def salesforce_package_status(
    org_id: str = Query(min_length=15, max_length=18),
    db: Session = Depends(get_db),
):
    prefix = f"Salesforce package setup {org_id}"
    item = (
        db.query(IntegrationConnection)
        .filter(
            IntegrationConnection.platform == "salesforce",
            IntegrationConnection.display_name == prefix,
        )
        .order_by(IntegrationConnection.created_at.desc())
        .first()
    )
    if not item:
        return {"status": "not_started", "connected": False}

    # A package setup can reach this state when Salesforce OAuth succeeds but
    # encrypted-principal activation is temporarily unavailable. Retry from
    # the already-authorized connection so the subscriber doesn't have to
    # repeat the login flow after a transient or corrected provisioning issue.
    if (
        item.status == "error"
        and item.workspace_id
        and item.instance_url
        and item.access_token_encrypted
        and item.last_error
        and "credential provisioning" in item.last_error.lower()
    ):
        account = (
            db.query(TrialAccount)
            .filter(TrialAccount.workspace_id == item.workspace_id)
            .first()
        )
        if account and account.secret_key:
            item.status = "provisioning"
            item.last_error = None
            db.commit()
            provisioned, provision_error = await _populate_salesforce_costpilot_credential(
                instance_url=item.instance_url,
                access_token=_decrypt(item.access_token_encrypted),
                secret_key=account.secret_key,
            )
            item.status = "connected" if provisioned else "error"
            item.last_success_at = datetime.utcnow() if provisioned else None
            item.last_error = None if provisioned else provision_error
            db.commit()

    return {
        "status": item.status,
        "connected": item.status == "connected",
        "workspace_id": item.workspace_id if item.status == "connected" else None,
        "last_error": item.last_error,
    }


@router.get("")
def list_connections(workspace_id: str = Query(default="default"), db: Session = Depends(get_db)):
    items = (
        db.query(IntegrationConnection)
        .filter(IntegrationConnection.workspace_id == workspace_id)
        .order_by(IntegrationConnection.created_at.desc())
        .all()
    )
    return {"connections": [_public_connection(item) for item in items]}


@router.post("", status_code=201)
def create_connection(body: ConnectionCreate, db: Session = Depends(get_db)):
    platform = body.platform.strip().lower()
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Unsupported discovery platform '{platform}'")
    item = IntegrationConnection(
        workspace_id=body.workspace_id.strip(),
        platform=platform,
        display_name=body.display_name.strip(),
        status="draft",
        auth_base_url=(
            _salesforce_auth_base(body.auth_base_url)
            if platform == "salesforce"
            else _servicenow_auth_base(body.auth_base_url)
            if platform == "servicenow"
            else body.auth_base_url
        ),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _public_connection(item)


@router.get("/{connection_id}")
def get_connection(connection_id: int, db: Session = Depends(get_db)):
    return _public_connection(_get_connection(db, connection_id))


@router.post("/{connection_id}/authorize")
def begin_authorization(connection_id: int, db: Session = Depends(get_db)):
    item = _get_connection(db, connection_id)
    if item.platform == "servicenow":
        client_id = os.getenv("SERVICENOW_CLIENT_ID")
        redirect_uri = os.getenv(
            "SERVICENOW_REDIRECT_URI",
            "https://fage-engine-21cb49fe4806.herokuapp.com/api/integrations/connections/oauth/servicenow/callback",
        )
        if not client_id:
            return {
                "configured": False,
                "platform": "servicenow",
                "required_environment": [
                    "SERVICENOW_CLIENT_ID",
                    "SERVICENOW_CLIENT_SECRET",
                    "CONNECTION_ENCRYPTION_KEY",
                ],
                "detail": (
                    "ServiceNow OAuth credentials are not configured yet. "
                    "Create an OAuth API endpoint for external clients in the instance."
                ),
            }
        state = secrets.token_urlsafe(32)
        item.oauth_state = state
        item.status = "authorizing"
        db.commit()
        auth_base = _servicenow_auth_base(item.auth_base_url)
        query = urlencode({
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        })
        return {"configured": True, "authorization_url": f"{auth_base}/oauth_auth.do?{query}"}
    if item.platform != "salesforce":
        return {
            "configured": False,
            "platform": item.platform,
            "detail": f"{item.platform.title()} discovery uses this same connection lifecycle; its OAuth adapter is next.",
        }
    client_id = os.getenv("SALESFORCE_CLIENT_ID")
    redirect_uri = os.getenv(
        "SALESFORCE_REDIRECT_URI",
        "https://fage-engine-21cb49fe4806.herokuapp.com/api/integrations/connections/oauth/salesforce/callback",
    )
    if not client_id:
        return {
            "configured": False,
            "platform": "salesforce",
            "required_environment": ["SALESFORCE_CLIENT_ID", "CONNECTION_ENCRYPTION_KEY"],
            "detail": "Salesforce OAuth credentials are not configured yet. Manual mapping remains available.",
        }
    state = secrets.token_urlsafe(32)
    pkce_verifier, pkce_challenge = _new_pkce_pair()
    mapping = json.loads(item.mapping_json) if item.mapping_json else {}
    mapping["_oauth_pkce_verifier"] = pkce_verifier
    item.mapping_json = json.dumps(mapping)
    item.oauth_state = state
    item.status = "authorizing"
    db.commit()
    auth_base = _salesforce_auth_base(item.auth_base_url)
    query = urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "api refresh_token",
        "state": state,
        "prompt": "login",
        "code_challenge": pkce_challenge,
        "code_challenge_method": "S256",
    })
    return {"configured": True, "authorization_url": f"{auth_base}/services/oauth2/authorize?{query}"}


@router.get("/oauth/salesforce/callback")
async def salesforce_callback(
    state: str,
    code: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
    db: Session = Depends(get_db),
):
    item = db.query(IntegrationConnection).filter(IntegrationConnection.oauth_state == state).first()
    if not item:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    if error:
        safe_error = re.sub(r"[^A-Za-z0-9_.-]", "_", error)[:100]
        description = (error_description or "Salesforce did not authorize the connection").strip()
        item.status = "error"
        item.last_error = description[:500]
        item.oauth_state = None
        mapping = json.loads(item.mapping_json) if item.mapping_json else {}
        mapping.pop("_oauth_pkce_verifier", None)
        item.mapping_json = json.dumps(mapping)
        db.commit()
        reason = (
            "cross_org_oauth"
            if error == "OAUTH_AUTHORIZATION_BLOCKED" and "Cross-org" in description
            else "oauth_denied"
        )
        return RedirectResponse(
            url=(
                f"/salesforce-setup.html?status=error"
                f"&reason={quote(reason)}&salesforce_error={quote(safe_error)}"
            )
        )
    if not code:
        item.status = "error"
        item.last_error = "Salesforce returned neither an authorization code nor an OAuth error"
        item.oauth_state = None
        mapping = json.loads(item.mapping_json) if item.mapping_json else {}
        mapping.pop("_oauth_pkce_verifier", None)
        item.mapping_json = json.dumps(mapping)
        db.commit()
        return RedirectResponse(
            url="/salesforce-setup.html?status=error&reason=oauth_incomplete"
        )
    client_id = os.getenv("SALESFORCE_CLIENT_ID")
    redirect_uri = os.getenv(
        "SALESFORCE_REDIRECT_URI",
        "https://fage-engine-21cb49fe4806.herokuapp.com/api/integrations/connections/oauth/salesforce/callback",
    )
    if not client_id:
        raise HTTPException(status_code=503, detail="Salesforce OAuth client ID is missing")
    mapping = json.loads(item.mapping_json) if item.mapping_json else {}
    pkce_verifier = str(mapping.get("_oauth_pkce_verifier") or "")
    if not pkce_verifier:
        item.status = "error"
        item.last_error = "Salesforce OAuth session must be restarted"
        item.oauth_state = None
        db.commit()
        return RedirectResponse(
            url="/salesforce-setup.html?status=error&reason=oauth_incomplete"
        )
    auth_base = _salesforce_auth_base(item.auth_base_url)
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{auth_base}/services/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "code_verifier": pkce_verifier,
                "redirect_uri": redirect_uri,
            },
        )
    if response.status_code >= 400:
        try:
            oauth_error = response.json()
        except (ValueError, TypeError):
            oauth_error = {}
        error_code = re.sub(
            r"[^A-Za-z0-9_.-]",
            "_",
            str(oauth_error.get("error") or f"http_{response.status_code}"),
        )[:100]
        error_description = str(
            oauth_error.get("error_description")
            or oauth_error.get("message")
            or "Salesforce rejected the OAuth token exchange"
        ).strip()[:400]
        item.status = "error"
        item.last_error = f"{error_code}: {error_description}"
        item.oauth_state = None
        mapping.pop("_oauth_pkce_verifier", None)
        item.mapping_json = json.dumps(mapping)
        db.commit()
        logger.warning(
            "Salesforce OAuth token exchange failed: status=%s error=%s description=%s",
            response.status_code,
            error_code,
            error_description,
        )
        return RedirectResponse(
            url=(
                "/salesforce-setup.html?status=error"
                "&reason=oauth_token_exchange"
                f"&salesforce_error={quote(error_code)}"
            )
        )
    token = response.json()
    access_token = token.get("access_token")
    if not access_token or not token.get("instance_url"):
        item.status = "error"
        item.last_error = "Salesforce authorization did not return an access token and instance URL"
        item.oauth_state = None
        mapping.pop("_oauth_pkce_verifier", None)
        item.mapping_json = json.dumps(mapping)
        db.commit()
        raise HTTPException(status_code=502, detail="Salesforce authorization was incomplete")

    identity = {}
    identity_url = token.get("id") or ""
    if identity_url:
        async with httpx.AsyncClient(timeout=20) as client:
            identity_response = await client.get(
                identity_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if identity_response.status_code < 400:
            identity = identity_response.json()

    mapping.pop("_oauth_pkce_verifier", None)
    item.mapping_json = json.dumps(mapping)
    package_setup = bool(mapping.get("package_setup"))
    external_org_id = str(
        identity.get("organization_id")
        or (identity_url.rstrip("/").split("/")[-2] if "/" in identity_url else "")
    )
    requested_org_id = str(mapping.get("requested_org_id") or "")
    if package_setup and requested_org_id and external_org_id != requested_org_id:
        item.status = "error"
        item.last_error = "The authorized Salesforce org did not match the installed package org"
        item.oauth_state = None
        db.commit()
        return RedirectResponse(
            url="/salesforce-setup.html?status=error&reason=org_mismatch"
        )

    item.access_token_encrypted = _encrypt(access_token)
    item.refresh_token_encrypted = _encrypt(token.get("refresh_token"))
    item.instance_url = token.get("instance_url")
    item.external_tenant_id = external_org_id or None
    item.oauth_state = None

    if package_setup:
        identity.setdefault("organization_id", external_org_id)
        account = _new_salesforce_workspace(identity, db)
        provisioned, provision_error = await _populate_salesforce_costpilot_credential(
            instance_url=item.instance_url,
            access_token=access_token,
            secret_key=account.secret_key,
        )
        item.workspace_id = account.workspace_id
        item.status = "connected" if provisioned else "error"
        item.last_success_at = datetime.utcnow() if provisioned else None
        item.last_error = None if provisioned else provision_error
        db.commit()
        if not provisioned:
            return RedirectResponse(
                url=(
                    f"/salesforce-setup.html?status=error"
                    f"&reason=credential_provisioning&connection_id={item.id}"
                )
            )
        return RedirectResponse(
            url=(
                f"/salesforce-setup.html?status=success"
                f"&workspace_id={quote(account.workspace_id)}"
                f"&org_id={quote(external_org_id)}"
            )
        )

    item.status = "connected"
    item.last_success_at = datetime.utcnow()
    item.last_error = None
    db.commit()
    return RedirectResponse(url=f"/onboarding.html?connection_id={item.id}&oauth=success")


@router.get("/oauth/servicenow/callback")
async def servicenow_callback(
    code: str,
    state: str,
    db: Session = Depends(get_db),
):
    item = (
        db.query(IntegrationConnection)
        .filter(
            IntegrationConnection.oauth_state == state,
            IntegrationConnection.platform == "servicenow",
        )
        .first()
    )
    if not item:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    client_id = os.getenv("SERVICENOW_CLIENT_ID")
    client_secret = os.getenv("SERVICENOW_CLIENT_SECRET")
    redirect_uri = os.getenv(
        "SERVICENOW_REDIRECT_URI",
        "https://fage-engine-21cb49fe4806.herokuapp.com/api/integrations/connections/oauth/servicenow/callback",
    )
    if not client_id or not client_secret:
        raise HTTPException(status_code=503, detail="ServiceNow OAuth credentials are incomplete")
    auth_base = _servicenow_auth_base(item.auth_base_url)
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{auth_base}/oauth_token.do",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
    if response.status_code >= 400:
        item.status = "error"
        item.last_error = "ServiceNow authorization failed"
        db.commit()
        raise HTTPException(status_code=502, detail="ServiceNow authorization failed")
    token = response.json()
    item.access_token_encrypted = _encrypt(token.get("access_token"))
    item.refresh_token_encrypted = _encrypt(token.get("refresh_token"))
    item.instance_url = auth_base
    item.external_tenant_id = (urlparse(auth_base).hostname or "").split(".")[0]
    item.oauth_state = None
    item.status = "connected"
    item.last_success_at = datetime.utcnow()
    item.last_error = None
    db.commit()
    return RedirectResponse(
        url=f"/onboarding.html?connection_id={item.id}&oauth=success&platform=servicenow"
    )


async def _salesforce_get(item: IntegrationConnection, path: str) -> dict:
    if not item.access_token_encrypted or not item.instance_url:
        raise HTTPException(status_code=409, detail="Connect Salesforce before discovering metadata")
    token = _decrypt(item.access_token_encrypted)
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.get(
            f"{item.instance_url.rstrip('/')}/services/data/{SALESFORCE_API_VERSION}/{path.lstrip('/')}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Salesforce metadata request failed ({response.status_code})")
    return response.json()


async def _salesforce_try_query(
    item: IntegrationConnection,
    query: str,
    *,
    tooling: bool = False,
) -> tuple[list[dict], Optional[str]]:
    """Query optional Salesforce metadata without failing the whole onboarding."""
    path = "tooling/query" if tooling else "query"
    try:
        payload = await _salesforce_get(item, f"{path}?q={quote(query, safe='')}")
        return payload.get("records", []), None
    except HTTPException as exc:
        return [], str(exc.detail)


@router.get("/{connection_id}/ai-entry-points")
async def discover_salesforce_ai_entry_points(
    connection_id: int,
    db: Session = Depends(get_db),
):
    """Discover existing Agentforce agents and Salesforce Flows for guided activation."""
    item = _get_connection(db, connection_id)
    if item.platform != "salesforce":
        raise HTTPException(status_code=400, detail="AI entry-point discovery currently supports Salesforce")

    # BotDefinition is the Tooling API representation used by Agentforce/Einstein
    # agents. Some trial orgs do not expose it, so this query is deliberately
    # optional and its permission failure is returned as a warning.
    agent_records, agent_error = await _salesforce_try_query(
        item,
        "SELECT Id, DeveloperName, MasterLabel FROM BotDefinition ORDER BY MasterLabel",
        tooling=True,
    )
    if agent_error:
        agent_records, agent_error = await _salesforce_try_query(
            item,
            "SELECT Id, DeveloperName, MasterLabel FROM Bot ORDER BY MasterLabel",
            tooling=True,
        )
    agents = [
        {
            "id": record.get("Id"),
            "name": record.get("DeveloperName") or record.get("MasterLabel") or record.get("Id"),
            "label": record.get("MasterLabel") or record.get("DeveloperName") or record.get("Id"),
            "status": "discovered",
            "costpilot_status": "action_required",
        }
        for record in agent_records
        if record.get("Id")
    ]

    flow_records, flow_error = await _salesforce_try_query(
        item,
        (
            "SELECT Id, DeveloperName, MasterLabel, ActiveVersionId, ProcessType "
            "FROM FlowDefinitionView ORDER BY MasterLabel"
        ),
        tooling=True,
    )
    if flow_error:
        flow_records, flow_error = await _salesforce_try_query(
            item,
            "SELECT Id, DeveloperName, MasterLabel, ActiveVersionId FROM FlowDefinitionView ORDER BY MasterLabel",
            tooling=True,
        )
    flows = [
        {
            "id": record.get("Id"),
            "name": record.get("DeveloperName") or record.get("MasterLabel") or record.get("Id"),
            "label": record.get("MasterLabel") or record.get("DeveloperName") or record.get("Id"),
            "active": bool(record.get("ActiveVersionId")),
            "process_type": record.get("ProcessType"),
            "trigger_type": record.get("TriggerType"),
            "status": "active" if record.get("ActiveVersionId") else "inactive",
            "costpilot_status": "action_required",
        }
        for record in flow_records
        if record.get("Id")
    ]

    warnings = []
    if agent_error:
        warnings.append(
            "Agentforce agents could not be listed with this org or OAuth user. "
            "You can enter an agent manually."
        )
    if flow_error:
        warnings.append(
            "Flows could not be listed with this org or OAuth user. "
            "You can enter a flow manually."
        )
    return {
        "connection_id": item.id,
        "agents": agents,
        "flows": flows,
        "warnings": warnings,
        "discovered_at": datetime.utcnow().isoformat(),
    }


async def _servicenow_table_get(
    item: IntegrationConnection,
    table: str,
    *,
    query: str,
    fields: str,
    limit: int = 500,
) -> list[dict]:
    if not item.access_token_encrypted or not item.instance_url:
        raise HTTPException(status_code=409, detail="Connect ServiceNow before discovering metadata")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", table):
        raise HTTPException(status_code=400, detail="Invalid ServiceNow metadata table")
    token = _decrypt(item.access_token_encrypted)
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.get(
            f"{item.instance_url.rstrip('/')}/api/now/table/{table}",
            params={
                "sysparm_query": query,
                "sysparm_fields": fields,
                "sysparm_limit": str(limit),
                "sysparm_display_value": "true",
                "sysparm_exclude_reference_link": "true",
            },
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
    if response.status_code >= 400:
        permission_hint = (
            " Verify that the OAuth user can read sys_db_object and sys_dictionary."
            if table in {"sys_db_object", "sys_dictionary"}
            else ""
        )
        raise HTTPException(
            status_code=502,
            detail=f"ServiceNow metadata request failed ({response.status_code}).{permission_hint}",
        )
    payload = response.json()
    return payload.get("result", []) if isinstance(payload, dict) else []


@router.get("/{connection_id}/objects")
async def discover_objects(connection_id: int, db: Session = Depends(get_db)):
    item = _get_connection(db, connection_id)
    if item.platform not in {"salesforce", "servicenow"}:
        raise HTTPException(status_code=501, detail=f"{item.platform.title()} metadata discovery is not available yet")
    try:
        if item.platform == "salesforce":
            payload = await _salesforce_get(item, "sobjects")
            objects = [
                {"name": obj["name"], "label": obj.get("label", obj["name"]), "custom": obj.get("custom", False)}
                for obj in payload.get("sobjects", [])
                if obj.get("queryable") and not obj.get("deprecatedAndHidden")
            ]
        else:
            rows = await _servicenow_table_get(
                item,
                "sys_db_object",
                query="nameISNOTEMPTY^nameNOT LIKEsys_^ORDERBYlabel",
                fields="sys_id,name,label,super_class",
                limit=1000,
            )
            objects = [
                {
                    "name": str(row.get("name") or ""),
                    "label": str(row.get("label") or row.get("name") or ""),
                    "custom": str(row.get("name") or "").startswith("u_"),
                    "recommended": str(row.get("name") or "") in SERVICENOW_DEFAULT_TABLES,
                }
                for row in rows
                if row.get("name")
            ]
        item.status = "discovering"
        item.last_success_at = datetime.utcnow()
        item.last_error = None
        db.commit()
        return {"connection_id": item.id, "objects": objects}
    except HTTPException as exc:
        item.status = "error"
        item.last_error = str(exc.detail)
        db.commit()
        raise


@router.post("/{connection_id}/discover")
async def discover_object_fields(
    connection_id: int,
    body: ObjectDiscoveryRequest,
    db: Session = Depends(get_db),
):
    item = _get_connection(db, connection_id)
    if item.platform not in {"salesforce", "servicenow"}:
        raise HTTPException(status_code=501, detail=f"{item.platform.title()} metadata discovery is not available yet")
    if item.platform == "salesforce":
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:__c|__mdt|__e|__x)?", body.object_name):
            raise HTTPException(status_code=400, detail="Invalid Salesforce object API name")
        payload = await _salesforce_get(item, f"sobjects/{quote(body.object_name, safe='')}/describe")
        fields = [
            {
                "name": field.get("name"),
                "label": field.get("label"),
                "type": field.get("type"),
                "readable": True,
                "writable": bool(field.get("createable") or field.get("updateable")),
                "reference_to": field.get("referenceTo") or [],
            }
            for field in payload.get("fields", [])
        ]
        object_label = payload.get("label", body.object_name)
        child_relationships = recommend_child_relationships(payload.get("childRelationships", []))
    else:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", body.object_name):
            raise HTTPException(status_code=400, detail="Invalid ServiceNow table name")
        dictionary_rows = await _servicenow_table_get(
            item,
            "sys_dictionary",
            query=f"name={body.object_name}^elementISNOTEMPTY^active=true^ORDERBYcolumn_label",
            fields="element,column_label,internal_type,reference,read_only,mandatory",
            limit=1000,
        )
        fields = [
            {
                "name": str(field.get("element") or ""),
                "label": str(field.get("column_label") or field.get("element") or ""),
                "type": str(field.get("internal_type") or ""),
                "readable": True,
                "writable": str(field.get("read_only") or "").lower() != "true",
                "reference_to": [str(field.get("reference"))] if field.get("reference") else [],
            }
            for field in dictionary_rows
            if field.get("element")
        ]
        if body.object_name in SERVICENOW_DEFAULT_TABLES:
            existing_names = {field["name"] for field in fields}
            fields.extend(
                dict(field)
                for field in SERVICENOW_INHERITED_TASK_FIELDS
                if field["name"] not in existing_names
            )
        relationship_rows = await _servicenow_table_get(
            item,
            "sys_dictionary",
            query=f"reference.name={body.object_name}^elementISNOTEMPTY^active=true",
            fields="name,element,column_label,reference",
            limit=500,
        )
        child_relationships = [
            {
                "object": str(row.get("name") or ""),
                "label": str(row.get("column_label") or row.get("name") or ""),
                "parent_field": str(row.get("element") or ""),
                "relationship_name": None,
                "cascade_delete": False,
                "confidence": "high" if str(row.get("name") or "").startswith("u_") else "medium",
                "score": 100 if str(row.get("name") or "").startswith("u_") else 70,
                "recommended_behavior": "track_and_rollup",
            }
            for row in relationship_rows
            if row.get("name") and row.get("element")
        ]
        child_relationships.sort(key=lambda value: (-value["score"], value["object"]))
        object_label = body.object_name.replace("_", " ").title()
    recommendations = recommend_business_mapping(fields)
    discovery = {
        "object": body.object_name,
        "object_label": object_label,
        "fields": fields,
        "recommendations": recommendations,
        "child_relationships": child_relationships,
        "discovered_at": datetime.utcnow().isoformat(),
    }
    item.selected_object = body.object_name
    item.discovery_json = json.dumps(discovery)
    item.status = "mapping"
    item.last_success_at = datetime.utcnow()
    item.last_error = None
    db.commit()
    return discovery


@router.put("/{connection_id}/mapping")
def approve_mapping(connection_id: int, body: MappingUpdate, db: Session = Depends(get_db)):
    item = _get_connection(db, connection_id)
    mapping = dict(body.mapping)
    children = mapping.get("children") or []
    if not isinstance(children, list) or len(children) > 100:
        raise HTTPException(status_code=400, detail="children must be a list of no more than 100 relationships")
    valid_behaviors = {"track_and_rollup", "rollup_only", "separate", "ignore"}
    normalized_children = []
    for child in children:
        if not isinstance(child, dict):
            raise HTTPException(status_code=400, detail="Each child relationship must be an object")
        child_object = str(child.get("object") or "").strip()
        parent_field = str(child.get("parent_field") or "").strip()
        behavior = str(child.get("behavior") or "track_and_rollup").strip().lower()
        if not child_object or not parent_field:
            raise HTTPException(status_code=400, detail="Each child relationship requires object and parent_field")
        if behavior not in valid_behaviors:
            raise HTTPException(status_code=400, detail=f"Unknown child attribution behavior '{behavior}'")
        normalized_children.append({
            "object": child_object,
            "label": str(child.get("label") or child_object).strip()[:200],
            "parent_field": parent_field,
            "relationship_name": str(child.get("relationship_name") or "").strip() or None,
            "behavior": behavior,
        })
    mapping["parent_object"] = body.selected_object
    mapping["children"] = normalized_children
    mapping["preserve_origin_record"] = True
    mapping.setdefault("unmapped_behavior", "separate")
    item.selected_object = body.selected_object
    item.mapping_json = json.dumps(mapping)
    item.status = "active"
    item.last_tested_at = datetime.utcnow()
    item.last_success_at = datetime.utcnow()
    item.last_error = None
    db.commit()
    db.refresh(item)
    return _public_connection(item)
