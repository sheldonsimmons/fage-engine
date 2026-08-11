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
from sqlalchemy import func
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import (
    AuditEvent, IntegrationConnection, TrialAccount,
    WorkAccount, WorkItem, WorkItemOutcome, WorkItemOutcomeEvent, WorkItemSourceLink,
)


router = APIRouter()
logger = logging.getLogger(__name__)
SUPPORTED_PLATFORMS = {"salesforce", "servicenow", "hubspot"}
# Platforms with real metadata discovery (sobjects/sys_db_object APIs) and
# outcome-sync adapters today. Single source of truth -- this used to be
# hardcoded as {"salesforce", "servicenow"} independently in four places in
# this file (plus duplicated again in two frontend JS files and a separate
# script), which is exactly the kind of drift that let CostPilot's actual
# platform support quietly diverge from what the UI claimed. Adding a new
# platform's discovery/outcome-sync support means updating this one set
# (and whatever dispatch function needs the new platform's specific
# adapter), not hunting across every file that used to guess independently.
DEEP_INTEGRATION_PLATFORMS = {"salesforce", "servicenow"}
SALESFORCE_API_VERSION = os.getenv("SALESFORCE_API_VERSION", "v65.0")
SALESFORCE_PACKAGE_VERSION_ID = os.getenv(
    "SALESFORCE_PACKAGE_VERSION_ID",
    "04tfj000000PZSPAA4",
)
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


class TrackedObjectsUpdate(BaseModel):
    objects: list[str] = Field(default_factory=list, max_length=50)


class ContextChangeDecision(BaseModel):
    decision: str = Field(pattern="^(approve|ignore)$")
    behavior: Optional[str] = Field(default=None, pattern="^(track_and_rollup|rollup_only|separate|ignore)$")


class AiEntryPointSelection(BaseModel):
    kind: str = Field(pattern="^(agent|flow)$")
    id: str = Field(default="", max_length=160)
    name: str = Field(min_length=1, max_length=240)
    label: str = Field(default="", max_length=240)


class AiEntryPointSelectionUpdate(BaseModel):
    entries: list[AiEntryPointSelection] = Field(default_factory=list, max_length=250)


class PackageRelationshipChild(BaseModel):
    object_name: str = Field(min_length=1, max_length=160)
    parent_field: str = Field(min_length=1, max_length=160)
    behavior: str = Field(
        default="track_and_rollup",
        pattern="^(track_and_rollup|rollup_only|separate|ignore)$",
    )


class PackageRelationshipApproval(BaseModel):
    parent_object: str = Field(default="Account", min_length=1, max_length=160)
    children: list[PackageRelationshipChild] = Field(default_factory=list, max_length=100)


def _salesforce_package_install_error(payload: dict) -> str:
    """Turn Salesforce installer output into customer-safe guidance."""
    raw_errors = payload.get("Errors") or payload.get("errors") or []
    if isinstance(raw_errors, dict):
        raw_errors = raw_errors.get("errors") or raw_errors.get("records") or [raw_errors]
    if not isinstance(raw_errors, list):
        raw_errors = [raw_errors]
    message = " ".join(
        str(value.get("message") or value.get("Message") or value)
        for value in raw_errors
        if value
    ).strip()
    lowered = message.lower()
    if "apex compile" in lowered or "compile failure" in lowered:
        return (
            "This Salesforce org requires existing Apex to compile during package installation. "
            "CostPilot did not change that code. Send this diagnostic to the Salesforce administrator; "
            "no CostPilot user should edit unrelated code."
        )
    return message[:500] or "Salesforce could not complete the CostPilot installation."


def _salesforce_oauth_error_reason(error: str, description: str) -> str:
    if error == "OAUTH_EC_APP_NOT_FOUND":
        return "package_required"
    if error == "OAUTH_AUTHORIZATION_BLOCKED" and "Cross-org" in description:
        return "cross_org_oauth"
    return "oauth_denied"


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


def _merge_salesforce_package_connection(
    pending: IntegrationConnection,
    workspace_id: str,
    db: Session,
) -> IntegrationConnection:
    """Promote a package OAuth attempt without creating a duplicate reconnect."""
    existing = (
        db.query(IntegrationConnection)
        .filter(
            IntegrationConnection.id != pending.id,
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.platform == pending.platform,
            IntegrationConnection.display_name == pending.display_name,
        )
        .order_by(IntegrationConnection.created_at.desc())
        .first()
    )
    if not existing:
        pending.workspace_id = workspace_id
        return pending

    # Keep one stable package connection per Salesforce org and workspace.
    # The new OAuth attempt owns the freshest tokens and instance metadata.
    existing.auth_base_url = pending.auth_base_url or existing.auth_base_url
    existing.instance_url = pending.instance_url
    existing.external_tenant_id = pending.external_tenant_id
    existing.access_token_encrypted = pending.access_token_encrypted
    existing.refresh_token_encrypted = (
        pending.refresh_token_encrypted or existing.refresh_token_encrypted
    )
    existing.oauth_state = None
    existing.mapping_json = pending.mapping_json or existing.mapping_json
    db.delete(pending)
    db.flush()
    return existing


def _salesforce_connection_progress(item: IntegrationConnection) -> tuple:
    mapping = _json_object(item.mapping_json)
    package_setup = mapping.get("package_setup")
    if not isinstance(package_setup, dict):
        package_setup = {}
    verification = package_setup.get("verification") or {}
    relationships = package_setup.get("relationships") or {}
    return (
        bool(package_setup.get("active")),
        bool(verification.get("verified")),
        bool(relationships.get("approved")),
        bool(mapping.get("selected_ai_entry_points")),
        bool(item.selected_object),
        item.updated_at or item.created_at or datetime.min,
    )


def _merge_salesforce_org_connection(
    pending: IntegrationConnection,
    db: Session,
) -> IntegrationConnection:
    """Reuse the most-progressed connection for a Salesforce org and workspace."""
    if not pending.external_tenant_id or not pending.workspace_id:
        return pending
    candidates = (
        db.query(IntegrationConnection)
        .filter(
            IntegrationConnection.id != pending.id,
            IntegrationConnection.workspace_id == pending.workspace_id,
            IntegrationConnection.platform == "salesforce",
            IntegrationConnection.external_tenant_id == pending.external_tenant_id,
            IntegrationConnection.status != "superseded",
        )
        .all()
    )
    if not candidates:
        return pending
    canonical = max(candidates, key=_salesforce_connection_progress)
    pending_mapping = _json_object(pending.mapping_json)
    canonical_mapping = _json_object(canonical.mapping_json)
    if isinstance(pending_mapping.get("salesforce_identity"), dict):
        canonical_mapping["salesforce_identity"] = pending_mapping["salesforce_identity"]
    canonical.auth_base_url = pending.auth_base_url or canonical.auth_base_url
    canonical.instance_url = pending.instance_url or canonical.instance_url
    canonical.access_token_encrypted = pending.access_token_encrypted
    canonical.refresh_token_encrypted = pending.refresh_token_encrypted or canonical.refresh_token_encrypted
    canonical.external_tenant_id = pending.external_tenant_id
    canonical.mapping_json = json.dumps(canonical_mapping)
    canonical.last_success_at = datetime.utcnow()
    canonical.last_error = None

    pending.status = "superseded"
    pending.last_error = f"Superseded by Salesforce connection {canonical.id}"
    pending.access_token_encrypted = None
    pending.refresh_token_encrypted = None
    pending.oauth_state = None
    pending_mapping["superseded_by_connection_id"] = canonical.id
    pending.mapping_json = json.dumps(pending_mapping)
    db.flush()
    return canonical


def _supersede_salesforce_siblings(item: IntegrationConnection, db: Session) -> None:
    """Retain duplicate history while removing it from the active lifecycle."""
    if not item.external_tenant_id:
        return
    siblings = (
        db.query(IntegrationConnection)
        .filter(
            IntegrationConnection.id != item.id,
            IntegrationConnection.workspace_id == item.workspace_id,
            IntegrationConnection.platform == "salesforce",
            IntegrationConnection.external_tenant_id == item.external_tenant_id,
            IntegrationConnection.status != "superseded",
        )
        .all()
    )
    for sibling in siblings:
        mapping = _json_object(sibling.mapping_json)
        mapping["superseded_by_connection_id"] = item.id
        sibling.mapping_json = json.dumps(mapping)
        sibling.status = "superseded"
        sibling.last_error = f"Superseded by active Salesforce connection {item.id}"
        sibling.access_token_encrypted = None
        sibling.refresh_token_encrypted = None


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
        # POST creates the encrypted principal value. Salesforce returns 409
        # when a value already exists (for example after reconnecting), in
        # which case PUT securely replaces it.
        if response.status_code == 409:
            response = await client.put(
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


def _public_connection(item: IntegrationConnection, db: Optional[Session] = None) -> dict:
    mapping = json.loads(item.mapping_json) if item.mapping_json else None
    if isinstance(mapping, dict):
        mapping = {
            key: value
            for key, value in mapping.items()
            if not str(key).startswith("_")
        }
    work_item_count = None
    if db is not None:
        # WorkItemSourceLink has no connection_id -- it's keyed by
        # (workspace_id, source_platform, source_record_id), so if a
        # workspace has two connections for the same platform this count
        # is shared between them rather than attributable to one. Accepted
        # ambiguity (see gap analysis) rather than a schema change here.
        from database.models import WorkItemSourceLink
        # source_platform is written capitalized ("Salesforce",
        # "ServiceNow") by the import/outcome-sync code, but
        # IntegrationConnection.platform is lowercase ("salesforce",
        # "servicenow") -- an exact-match filter here silently matched zero
        # rows even for connections with hundreds of real linked records.
        work_item_count = (
            db.query(WorkItemSourceLink)
            .filter(
                WorkItemSourceLink.workspace_id == item.workspace_id,
                func.lower(WorkItemSourceLink.source_platform) == item.platform.lower(),
            )
            .count()
        )
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
        "tracked_objects": json.loads(item.tracked_objects_json) if item.tracked_objects_json else [],
        "supports_outcome_sync": item.platform in DEEP_INTEGRATION_PLATFORMS,
        "mapping": mapping,
        "last_tested_at": item.last_tested_at,
        "last_success_at": item.last_success_at,
        "last_outcome_sync_at": item.last_outcome_sync_at,
        "last_error": item.last_error,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "work_item_count": work_item_count,
    }


def _get_connection(db: Session, connection_id: int) -> IntegrationConnection:
    item = db.query(IntegrationConnection).filter(IntegrationConnection.id == connection_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Connection was not found")
    return item


def _require_connected_salesforce(item: IntegrationConnection) -> tuple[str, str]:
    if item.platform != "salesforce":
        raise HTTPException(status_code=400, detail="This action requires a Salesforce connection")
    if not item.instance_url or not item.access_token_encrypted:
        raise HTTPException(status_code=409, detail="Connect Salesforce before installing CostPilot")
    return item.instance_url.rstrip("/"), _decrypt(item.access_token_encrypted)


def _require_connected(item: IntegrationConnection) -> None:
    """Platform-agnostic connectivity check for actions (bulk import,
    outcome sync) that don't need the instance_url/token tuple
    _require_connected_salesforce returns -- just a live connection."""
    if not item.instance_url or not item.access_token_encrypted:
        raise HTTPException(
            status_code=409,
            detail=f"Connect {item.platform.title()} before importing business context",
        )


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


def recommend_child_relationships(
    relationships: list[dict],
    parent_object: Optional[str] = None,
) -> list[dict]:
    """Rank business-useful direct children and remove Salesforce schema noise."""
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
    ignored_prefixes = (
        "Apex", "Async", "Auth", "CollaborationGroup", "Content", "Cron",
        "DuplicateRecord", "Email", "EntitySubscription", "Flow", "Login",
        "Network", "Permission", "Process", "RecordAction", "RecordAlert",
        "Setup", "User",
    )
    business_scores = {
        "Opportunity": (100, "Revenue and pipeline activity"),
        "Case": (98, "Customer service activity"),
        "Contact": (94, "Customer stakeholder activity"),
        "Contract": (92, "Commercial agreement activity"),
        "Order": (90, "Customer order activity"),
        "Quote": (88, "Pricing and proposal activity"),
        "WorkOrder": (88, "Service delivery activity"),
        "Asset": (86, "Customer asset activity"),
        "Campaign": (84, "Marketing activity"),
    }
    normalized_parent = str(parent_object or "").strip().lower()
    for relationship in relationships:
        child_object = str(relationship.get("childSObject") or "").strip()
        parent_field = str(relationship.get("field") or "").strip()
        relationship_name = str(relationship.get("relationshipName") or "").strip()
        if (
            not child_object
            or not parent_field
            or (normalized_parent and child_object.lower() == normalized_parent)
            or child_object in ignored_objects
            or child_object.endswith(ignored_suffixes)
            or child_object.startswith(ignored_prefixes)
            or not relationship_name
        ):
            continue
        is_custom = child_object.endswith("__c")
        if child_object in business_scores:
            score, reason = business_scores[child_object]
        elif is_custom:
            score, reason = 82, "Custom business record linked to this parent"
        else:
            score, reason = 45, "Direct Salesforce relationship"
        recommended = score >= 80
        suggestions.append({
            "object": child_object,
            "label": child_object.removesuffix("__c").replace("_", " "),
            "parent_field": parent_field,
            "relationship_name": relationship_name or None,
            "cascade_delete": bool(relationship.get("cascadeDelete")),
            "confidence": "high" if score >= 90 else "medium" if score >= 80 else "low",
            "score": score,
            "recommended": recommended,
            "recommendation_reason": reason,
            "recommended_behavior": "track_and_rollup",
        })
    canonical_fields = {
        f"{str(parent_object or '').strip()}Id".lower(),
        f"{str(parent_object or '').strip()}__c".lower(),
    } if parent_object else set()
    deduplicated: dict[str, dict] = {}
    for suggestion in suggestions:
        key = suggestion["object"].lower()
        current = deduplicated.get(key)
        preference = (
            suggestion["parent_field"].lower() in canonical_fields,
            suggestion["score"],
            bool(suggestion["relationship_name"]),
        )
        current_preference = (
            current["parent_field"].lower() in canonical_fields,
            current["score"],
            bool(current["relationship_name"]),
        ) if current else None
        if current is None or preference > current_preference:
            deduplicated[key] = suggestion
    ranked = list(deduplicated.values())
    ranked.sort(key=lambda item: (-item["score"], item["label"].lower(), item["object"]))
    return ranked


def _json_object(raw: Optional[str]) -> dict:
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _context_change_id(kind: str, value: dict) -> str:
    identity = "|".join([
        kind,
        str(value.get("object") or ""),
        str(value.get("parent_field") or ""),
        str(value.get("relationship_name") or ""),
    ])
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def _build_context_snapshot(objects: list[dict], parent_object: str, relationships: list[dict]) -> dict:
    captured_at = datetime.utcnow().isoformat()
    return {
        "captured_at": captured_at,
        "parent_object": parent_object,
        "objects": {
            str(obj.get("name")): {
                "object": str(obj.get("name")),
                "label": str(obj.get("label") or obj.get("name")),
                "custom": bool(obj.get("custom")),
            }
            for obj in objects if obj.get("name")
        },
        "relationships": {
            _context_change_id("relationship_added", rel): {
                "object": str(rel.get("object") or ""),
                "label": str(rel.get("label") or rel.get("object") or ""),
                "parent_field": str(rel.get("parent_field") or ""),
                "relationship_name": rel.get("relationship_name"),
                "parent_object": parent_object,
            }
            for rel in relationships if rel.get("object") and rel.get("parent_field")
        },
    }


def _new_context_changes(previous: dict, current: dict) -> list[dict]:
    discovered_at = current.get("captured_at") or datetime.utcnow().isoformat()
    changes = []
    for name, obj in current.get("objects", {}).items():
        if name not in previous.get("objects", {}):
            value = dict(obj)
            value.update({
                "id": _context_change_id("object_added", obj),
                "kind": "object_added",
                "status": "pending",
                "discovered_at": discovered_at,
                "suggested_behavior": "separate",
            })
            changes.append(value)
    for change_id, relationship in current.get("relationships", {}).items():
        if change_id not in previous.get("relationships", {}):
            value = dict(relationship)
            value.update({
                "id": change_id,
                "kind": "relationship_added",
                "status": "pending",
                "discovered_at": discovered_at,
                "suggested_behavior": "track_and_rollup",
            })
            changes.append(value)
    return changes


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
            IntegrationConnection.status == "connected",
        )
        .order_by(IntegrationConnection.created_at.desc())
        .first()
    ) or (
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
        "connection_id": item.id if item.status == "connected" else None,
        "last_error": item.last_error,
    }


@router.get("")
def list_connections(
    workspace_id: str = Query(default="default"),
    include_superseded: bool = False,
    db: Session = Depends(get_db),
):
    query = db.query(IntegrationConnection).filter(IntegrationConnection.workspace_id == workspace_id)
    if not include_superseded:
        query = query.filter(IntegrationConnection.status != "superseded")
    items = query.order_by(IntegrationConnection.created_at.desc()).all()
    return {"connections": [_public_connection(item, db) for item in items]}


@router.get("/health")
def workspace_connection_health(workspace_id: str = Query(default="default"), db: Session = Depends(get_db)):
    """
    A rule-based, evidence-derived health score for how "onboarded" a
    workspace actually is -- every number here comes from a real query
    against existing tables, no separately-tracked score to drift out of
    sync with reality. Categories are intentionally simple (no ML/weighting
    tuning) so each number is explainable in one sentence.
    """
    from database.models import TokenTransaction

    connections = (
        db.query(IntegrationConnection)
        .filter(IntegrationConnection.workspace_id == workspace_id, IntegrationConnection.status != "superseded")
        .all()
    )
    healthy_connections = [c for c in connections if c.last_success_at and c.status != "error"]
    syncable_connections = [c for c in healthy_connections if c.platform in DEEP_INTEGRATION_PLATFORMS]

    total_calls = db.query(func.count(TokenTransaction.id)).filter(TokenTransaction.workspace_id == workspace_id).scalar() or 0
    resolved_calls = (
        db.query(func.count(TokenTransaction.id))
        .filter(TokenTransaction.workspace_id == workspace_id, TokenTransaction.work_item_id.isnot(None))
        .scalar() or 0
    )
    work_item_count = db.query(func.count(WorkItem.id)).filter(WorkItem.workspace_id == workspace_id).scalar() or 0
    outcome_count = db.query(func.count(WorkItemOutcome.id)).filter(WorkItemOutcome.workspace_id == workspace_id).scalar() or 0

    def pct(numerator: float, denominator: float, if_no_denominator: float = 0.0) -> float:
        if not denominator:
            return if_no_denominator
        return round(min(100.0, (numerator / denominator) * 100), 1)

    ai_sources_pct = 100.0 if (healthy_connections or total_calls > 0) else 0.0
    business_context_pct = 100.0 if work_item_count > 0 else 0.0
    field_mapping_pct = pct(
        sum(1 for c in healthy_connections if c.mapping_json), len(healthy_connections), if_no_denominator=0.0,
    )
    outcome_coverage_pct = pct(outcome_count, work_item_count, if_no_denominator=0.0)
    fresh_syncs = sum(
        1 for c in syncable_connections
        if c.last_outcome_sync_at and (datetime.utcnow() - c.last_outcome_sync_at) < timedelta(hours=24)
    )
    # No syncable connection is not itself unhealthy -- treated as 100 so a
    # workspace with only SDK/gateway activity (no CRM connected yet) isn't
    # penalized for a category that doesn't apply to it yet.
    sync_health_pct = pct(fresh_syncs, len(syncable_connections), if_no_denominator=100.0)
    data_quality_pct = pct(resolved_calls, total_calls, if_no_denominator=0.0)

    categories = {
        "ai_sources":       ai_sources_pct,
        "business_context": business_context_pct,
        "field_mapping":    field_mapping_pct,
        "outcome_coverage": outcome_coverage_pct,
        "sync_health":       sync_health_pct,
        "data_quality":      data_quality_pct,
    }
    overall = round(sum(categories.values()) / len(categories), 1)

    recommendations = []
    if ai_sources_pct < 100:
        recommendations.append("Connect an AI source (SDK, gateway, or a native connector) so activity starts flowing in.")
    if business_context_pct < 100:
        recommendations.append("Connect a business system (e.g. Salesforce) so AI activity can roll up to real work.")
    if 0 < outcome_coverage_pct < 80:
        recommendations.append("Some work items have no outcome data yet -- run Sync Now or wait for the next automatic sync.")
    elif outcome_coverage_pct == 0 and work_item_count > 0:
        recommendations.append("Connect outcome fields (e.g. Opportunity stage/amount) to unlock business-value analytics.")
    if syncable_connections and sync_health_pct < 100:
        recommendations.append(f"{len(syncable_connections) - fresh_syncs} connection(s) haven't synced in over 24 hours.")
    if 0 < data_quality_pct < 80:
        recommendations.append(f"{total_calls - resolved_calls} AI events are missing work context and aren't attributed to any work item.")

    return {
        "workspace_id": workspace_id,
        "overall": overall,
        "categories": categories,
        "recommendations": recommendations,
    }


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


@router.post("/{connection_id}/salesforce-package-install")
async def install_salesforce_package(connection_id: int, db: Session = Depends(get_db)):
    """Install CostPilot without compiling unrelated subscriber Apex."""
    item = _get_connection(db, connection_id)
    instance_url, access_token = _require_connected_salesforce(item)
    mapping = _json_object(item.mapping_json)
    install = mapping.get("salesforce_package_install")
    if not isinstance(install, dict):
        install = {}
    if install.get("request_id") and install.get("status") in {"in_progress", "success"}:
        return {
            "status": install["status"],
            "installed": install["status"] == "success",
            "message": install.get("message"),
        }

    endpoint = f"{instance_url}/services/data/{SALESFORCE_API_VERSION}/tooling/sobjects/PackageInstallRequest"
    payload = {
        "SubscriberPackageVersionKey": SALESFORCE_PACKAGE_VERSION_ID,
        "NameConflictResolution": "Block",
        "SecurityType": "None",
        "PackageInstallSource": "U",
        "ApexCompileType": "package",
        "UpgradeType": "mixed-mode",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
            json=payload,
        )
    try:
        result = response.json()
    except (TypeError, ValueError):
        result = {}
    if response.status_code >= 400 or not result.get("id"):
        message = _salesforce_package_install_error(result)
        mapping["salesforce_package_install"] = {"status": "error", "message": message}
        item.mapping_json = json.dumps(mapping)
        item.last_error = message
        db.commit()
        raise HTTPException(status_code=502, detail=message)

    mapping["salesforce_package_install"] = {
        "request_id": result["id"],
        "status": "in_progress",
        "message": "Salesforce is installing CostPilot. You can keep this page open.",
    }
    item.mapping_json = json.dumps(mapping)
    item.last_error = None
    db.commit()
    return {
        "status": "in_progress",
        "installed": False,
        "message": "Salesforce is installing CostPilot. You can keep this page open.",
    }


@router.get("/{connection_id}/salesforce-package-install")
async def get_salesforce_package_install(connection_id: int, db: Session = Depends(get_db)):
    item = _get_connection(db, connection_id)
    instance_url, access_token = _require_connected_salesforce(item)
    mapping = _json_object(item.mapping_json)
    install = mapping.get("salesforce_package_install")
    if not isinstance(install, dict) or not install.get("request_id"):
        return {"status": "not_started", "installed": False}

    request_id = quote(str(install["request_id"]), safe="")
    endpoint = (
        f"{instance_url}/services/data/{SALESFORCE_API_VERSION}"
        f"/tooling/sobjects/PackageInstallRequest/{request_id}"
    )
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(endpoint, headers={"Authorization": f"Bearer {access_token}"})
    try:
        result = response.json()
    except (TypeError, ValueError):
        result = {}
    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail="CostPilot could not read Salesforce installation progress. Try again shortly.",
        )

    raw_status = str(result.get("Status") or "UNKNOWN").upper()
    status = {
        "SUCCESS": "success",
        "IN_PROGRESS": "in_progress",
        "ERROR": "error",
        "CANCELED": "error",
    }.get(raw_status, "in_progress")
    message = (
        "CostPilot is installed and ready for guided setup."
        if status == "success"
        else _salesforce_package_install_error(result)
        if status == "error"
        else "Salesforce is installing CostPilot. You can keep this page open."
    )
    install.update({"status": status, "message": message})
    mapping["salesforce_package_install"] = install
    item.mapping_json = json.dumps(mapping)
    item.last_error = message if status == "error" else None
    db.commit()
    return {"status": status, "installed": status == "success", "message": message}


@router.get("/{connection_id}")
def get_connection(connection_id: int, db: Session = Depends(get_db)):
    return _public_connection(_get_connection(db, connection_id), db)


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
        reason = _salesforce_oauth_error_reason(error, description)
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
    mapping["salesforce_identity"] = {
        "username": identity.get("username") or identity.get("email"),
        "display_name": identity.get("display_name") or identity.get("name"),
    }
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
        item = _merge_salesforce_package_connection(item, account.workspace_id, db)
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
    item = _merge_salesforce_org_connection(item, db)
    if item.status not in {"active", "mapping"}:
        item.status = "connected"
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


async def _salesforce_refresh_access_token(db: Session, item: IntegrationConnection) -> bool:
    """
    Exchange the stored refresh_token for a new access_token. Salesforce
    access tokens are short-lived (~2 hours); nothing previously renewed
    them, so every connection silently went stale until someone manually
    re-authorized it (see connect flow in this file for the original
    exchange this mirrors). Refresh token rotation is enabled on the
    Connected App (isRefreshTokenRotationEnabled), so a new refresh_token
    must be stored too when Salesforce returns one, or the next refresh
    would fail with the old one already invalidated.
    """
    if not item.refresh_token_encrypted or not item.instance_url:
        return False
    client_id = os.getenv("SALESFORCE_CLIENT_ID")
    if not client_id:
        return False
    auth_base = _salesforce_auth_base(item.auth_base_url or item.instance_url)
    refresh_token = _decrypt(item.refresh_token_encrypted)
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{auth_base}/services/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
        )
    if response.status_code >= 400:
        return False
    token = response.json()
    access_token = token.get("access_token")
    if not access_token:
        return False
    item.access_token_encrypted = _encrypt(access_token)
    if token.get("refresh_token"):
        item.refresh_token_encrypted = _encrypt(token["refresh_token"])
    if token.get("instance_url"):
        item.instance_url = token["instance_url"]
    item.last_error = None
    db.commit()
    return True


async def _salesforce_get(
    item: IntegrationConnection, path: str, *, db: Optional[Session] = None
) -> dict:
    if not item.access_token_encrypted or not item.instance_url:
        raise HTTPException(status_code=409, detail="Connect Salesforce before discovering metadata")

    async def _do_request():
        token = _decrypt(item.access_token_encrypted)
        async with httpx.AsyncClient(timeout=25) as client:
            return await client.get(
                f"{item.instance_url.rstrip('/')}/services/data/{SALESFORCE_API_VERSION}/{path.lstrip('/')}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )

    response = await _do_request()
    if response.status_code == 401 and db is not None:
        # Expired access token -- try the refresh token once before giving
        # up, instead of surfacing an avoidable failure to the caller.
        if await _salesforce_refresh_access_token(db, item):
            response = await _do_request()
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Salesforce metadata request failed ({response.status_code})")
    return response.json()


async def _salesforce_try_query(
    item: IntegrationConnection,
    query: str,
    *,
    tooling: bool = False,
    db: Optional[Session] = None,
) -> tuple[list[dict], Optional[str]]:
    """Query optional Salesforce metadata without failing the whole onboarding."""
    path = "tooling/query" if tooling else "query"
    try:
        payload = await _salesforce_get(item, f"{path}?q={quote(query, safe='')}", db=db)
        return payload.get("records", []), None
    except HTTPException as exc:
        return [], str(exc.detail)


async def _salesforce_query_all(
    item: IntegrationConnection,
    query: str,
    *,
    db: Optional[Session] = None,
    max_records: int = 50_000,
) -> tuple[list[dict], Optional[str]]:
    """
    Like _salesforce_try_query, but follows Salesforce's nextRecordsUrl
    pagination (query results cap at 2,000 records per page) to collect
    every matching record -- needed for bulk import, where "all
    Opportunities" for a real org can be thousands of rows, unlike the
    outcome sync's bounded WHERE Id IN (...) batches.

    max_records is a hard safety cap, not an expected ceiling, so a
    runaway query against an unexpectedly huge org can't loop forever.
    """
    try:
        payload = await _salesforce_get(item, f"query?q={quote(query, safe='')}", db=db)
    except HTTPException as exc:
        return [], str(exc.detail)

    records = list(payload.get("records", []))
    next_url = payload.get("nextRecordsUrl")
    data_api_prefix = f"/services/data/{SALESFORCE_API_VERSION}/"
    while next_url and not payload.get("done", True) and len(records) < max_records:
        path = next_url[len(data_api_prefix):] if next_url.startswith(data_api_prefix) else next_url.lstrip("/")
        try:
            payload = await _salesforce_get(item, path, db=db)
        except HTTPException as exc:
            return records, str(exc.detail)
        records.extend(payload.get("records", []))
        next_url = payload.get("nextRecordsUrl")
    return records, None


async def _servicenow_query_all(
    item: IntegrationConnection,
    table: str,
    *,
    query: str,
    fields: str,
    db: Optional[Session] = None,
    page_size: int = 500,
    max_records: int = 50_000,
) -> tuple[list[dict], Optional[str]]:
    """
    Like _salesforce_query_all, but for ServiceNow's Table API, which has
    no cursor field to follow (no nextRecordsUrl/done) -- pagination is
    sysparm_offset/sysparm_limit instead, and end-of-results is inferred
    from a page coming back shorter than the requested page_size. This is
    the piece that actually proves the connector abstraction generalizes:
    Salesforce and ServiceNow need genuinely different fetch-all
    strategies, not just different field names.
    """
    records: list[dict] = []
    offset = 0
    while len(records) < max_records:
        try:
            page = await _servicenow_table_get(
                item, table, query=query, fields=fields,
                limit=page_size, offset=offset, db=db,
                display_value="all",
            )
        except HTTPException as exc:
            return records, str(exc.detail)
        records.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return records, None


@router.get("/{connection_id}/ai-entry-points")
async def discover_salesforce_ai_entry_points(
    connection_id: int,
    db: Session = Depends(get_db),
):
    """Discover existing Agentforce agents and Salesforce Flows for guided activation."""
    item = _get_connection(db, connection_id)
    if item.platform != "salesforce":
        raise HTTPException(status_code=400, detail="AI entry-point discovery currently supports Salesforce")

    # Current Agentforce orgs expose agents as GenAiPlannerDefinition records.
    # Older Einstein Bots used BotDefinition/Bot, so retain those as fallbacks
    # for orgs that have not moved to the Agentforce planner metadata model.
    agent_records, agent_error = await _salesforce_try_query(
        item,
        (
            "SELECT Id, DeveloperName, MasterLabel, PlannerType "
            "FROM GenAiPlannerDefinition ORDER BY MasterLabel"
        ),
        tooling=True,
    )
    if agent_error:
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
            "planner_type": record.get("PlannerType"),
            "status": "discovered",
            "costpilot_status": "action_required",
        }
        for record in agent_records
        if record.get("Id")
    ]

    flow_records, flow_error = await _salesforce_try_query(
        item,
        (
            "SELECT Id, DeveloperName, MasterLabel, ActiveVersionId "
            "FROM FlowDefinition ORDER BY DeveloperName"
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
        "selected": (
            json.loads(item.mapping_json or "{}").get("selected_ai_entry_points", [])
        ),
        "warnings": warnings,
        "discovered_at": datetime.utcnow().isoformat(),
    }


@router.post("/{connection_id}/ai-entry-points/selection")
def save_salesforce_ai_entry_points(
    connection_id: int,
    payload: AiEntryPointSelectionUpdate,
    db: Session = Depends(get_db),
):
    """Persist the Agentforce agents and Flows approved during package setup."""
    item = _get_connection(db, connection_id)
    if item.platform != "salesforce":
        raise HTTPException(status_code=400, detail="AI entry-point selection currently supports Salesforce")
    mapping = json.loads(item.mapping_json or "{}")

    def normalize(entries: list[dict]) -> list[dict]:
        unique = {}
        for entry in entries:
            normalized = {
                "kind": str(entry.get("kind") or ""),
                "id": str(entry.get("id") or ""),
                "name": str(entry.get("name") or "").strip(),
                "label": str(entry.get("label") or entry.get("name") or "").strip(),
                "activation_status": "action_required",
            }
            key = (normalized["kind"].lower(), normalized["name"].lower())
            current = unique.get(key)
            current_id = str(current.get("id") or "") if current else ""
            candidate_id = normalized["id"]
            candidate_is_discovered = bool(candidate_id and not candidate_id.startswith("manual:"))
            current_is_discovered = bool(current_id and not current_id.startswith("manual:"))
            if current is None or (candidate_is_discovered and not current_is_discovered):
                unique[key] = normalized
        return list(unique.values())

    selected = normalize([
        {
            "kind": entry.kind,
            "id": entry.id,
            "name": entry.name,
            "label": entry.label or entry.name,
        }
        for entry in payload.entries
    ])
    previous = normalize(mapping.get("selected_ai_entry_points") or [])
    mapping["selected_ai_entry_points"] = selected
    if selected != previous or not mapping.get("entry_points_selected_at"):
        mapping["entry_points_selected_at"] = datetime.utcnow().isoformat()
        package_setup = mapping.get("package_setup")
        if isinstance(package_setup, dict):
            package_setup["verification"] = {
                "verified": False,
                "parent_verified": False,
                "child_verified": False,
                "message": "AI entry-point selection changed; run fresh parent and related-record checks.",
            }
    item.mapping_json = json.dumps(mapping)
    db.commit()
    return {
        "connection_id": item.id,
        "selected": selected,
        "count": len(selected),
    }


def _salesforce_package_setup(item: IntegrationConnection) -> dict:
    mapping = _json_object(item.mapping_json)
    identity = mapping.get("salesforce_identity")
    if not isinstance(identity, dict):
        identity = {}
    package_setup = mapping.get("package_setup")
    if not isinstance(package_setup, dict):
        package_setup = {}
    relationships = package_setup.get("relationships")
    if not isinstance(relationships, dict):
        relationships = {
            "approved": False,
            "parent_object": mapping.get("parent_object") or "Account",
            "children": mapping.get("children") or [
                {"object_name": "Contact", "parent_field": "AccountId", "behavior": "track_and_rollup"},
                {"object_name": "Opportunity", "parent_field": "AccountId", "behavior": "track_and_rollup"},
                {"object_name": "Case", "parent_field": "AccountId", "behavior": "track_and_rollup"},
            ],
        }
    verification = package_setup.get("verification")
    if not isinstance(verification, dict):
        verification = {"verified": False}
    selected = mapping.get("selected_ai_entry_points")
    if not isinstance(selected, list):
        selected = []
    org_verified = bool(item.external_tenant_id and item.instance_url and item.status in {"connected", "active"})
    workspace_bound = bool(item.workspace_id and not item.workspace_id.startswith("pending-"))
    parent_verified = bool(verification.get("parent_verified"))
    child_verified = bool(verification.get("child_verified"))
    checklist = {
        "org_verified": org_verified,
        "workspace_bound": workspace_bound,
        "relationships_approved": bool(relationships.get("approved")),
        "entry_points_selected": bool(selected),
        "parent_request_verified": parent_verified,
        "child_request_verified": child_verified,
    }
    labels = {
        "org_verified": "connect and verify the installed Salesforce org",
        "workspace_bound": "bind the Salesforce org to one CostPilot workspace",
        "relationships_approved": "approve the Account and related-record mapping",
        "entry_points_selected": "choose at least one Agentforce agent or Flow",
        "parent_request_verified": "run one live request from an Account",
        "child_request_verified": "run one live request from an approved related record",
    }
    missing = [labels[key] for key, passed in checklist.items() if not passed]
    return {
        "connection_id": item.id,
        "workspace_id": item.workspace_id,
        "connection_status": item.status,
        "org": {
            "organization_id": item.external_tenant_id,
            "instance_url": item.instance_url,
            "username": identity.get("username"),
            "display_name": identity.get("display_name"),
        },
        "selected": selected,
        "relationships": relationships,
        "verification": verification,
        "checklist": checklist,
        "missing": missing,
        "ready_to_activate": not missing,
        "active": bool(package_setup.get("active")),
        "go_live_at": package_setup.get("go_live_at"),
    }


@router.get("/{connection_id}/package-setup")
def get_salesforce_package_setup(
    connection_id: int,
    db: Session = Depends(get_db),
):
    """Return the durable state used by the packaged five-step Salesforce wizard."""
    item = _get_connection(db, connection_id)
    if item.platform != "salesforce":
        raise HTTPException(status_code=400, detail="Package setup currently supports Salesforce")
    return _salesforce_package_setup(item)


@router.post("/{connection_id}/package-setup/relationships")
def approve_salesforce_package_relationships(
    connection_id: int,
    payload: PackageRelationshipApproval,
    db: Session = Depends(get_db),
):
    """Approve the parent and related records that share one business context."""
    item = _get_connection(db, connection_id)
    if item.platform != "salesforce":
        raise HTTPException(status_code=400, detail="Package setup currently supports Salesforce")
    mapping = _json_object(item.mapping_json)
    package_setup = mapping.get("package_setup")
    if not isinstance(package_setup, dict):
        package_setup = {}
        mapping["package_setup"] = package_setup
    children = [child.model_dump() for child in payload.children]
    package_setup["relationships"] = {
        "approved": True,
        "approved_at": datetime.utcnow().isoformat(),
        "parent_object": payload.parent_object,
        "children": children,
    }
    # Keep the universal mapper and the package wizard on the same contract.
    mapping["parent_object"] = payload.parent_object
    mapping["children"] = children
    mapping["preserve_origin_record"] = True
    mapping.setdefault("unmapped_behavior", "separate")
    item.selected_object = payload.parent_object
    item.mapping_json = json.dumps(mapping)
    db.commit()
    return _salesforce_package_setup(item)


@router.post("/{connection_id}/package-setup/verify")
def verify_salesforce_package_request(
    connection_id: int,
    db: Session = Depends(get_db),
):
    """Confirm that a real Salesforce request reached the governed audit pipeline."""
    item = _get_connection(db, connection_id)
    if item.platform != "salesforce":
        raise HTTPException(status_code=400, detail="Package setup currently supports Salesforce")
    mapping = _json_object(item.mapping_json)
    package_setup = mapping.get("package_setup")
    if not isinstance(package_setup, dict):
        package_setup = {}
        mapping["package_setup"] = package_setup
    prior_verification = package_setup.get("verification") or {}
    selected_at_raw = mapping.get("entry_points_selected_at")
    selected_at = None
    if selected_at_raw:
        try:
            selected_at = datetime.fromisoformat(str(selected_at_raw).replace("Z", "+00:00")).replace(tzinfo=None)
        except (TypeError, ValueError):
            selected_at = None
    query = db.query(AuditEvent).filter(
        AuditEvent.workspace_id == item.workspace_id,
        AuditEvent.is_simulation.is_(False),
        func.lower(AuditEvent.actor_source_platform) == "salesforce",
    )
    # A completed verification is durable across a page reload. A real
    # selection change clears verification in save_salesforce_ai_entry_points.
    if selected_at is not None and not prior_verification.get("verified"):
        query = query.filter(AuditEvent.timestamp >= selected_at)
    relationships = package_setup.get("relationships") or {}
    parent_type = str(relationships.get("parent_object") or "Account").lower()
    child_types = {
        str(child.get("object_name") or child.get("object") or "").lower()
        for child in relationships.get("children") or []
        if child.get("behavior") in {"track_and_rollup", "rollup_only"}
    }
    # Salesforce exposes Account.ParentId as a child relationship. A parent
    # request must never satisfy the separate related-record readiness check.
    child_types.discard(parent_type)
    events = query.order_by(AuditEvent.timestamp.desc(), AuditEvent.id.desc()).all()
    parent_events = [row for row in events if str(row.origin_record_type or "").lower() == parent_type]
    child_events = [row for row in events if str(row.origin_record_type or "").lower() in child_types]
    matching_pair = next(
        (
            (parent, child)
            for parent in parent_events
            for child in child_events
            if parent.id != child.id
            and parent.work_item_id
            and parent.work_item_id == child.work_item_id
        ),
        None,
    )
    parent_event = matching_pair[0] if matching_pair else (parent_events[0] if parent_events else None)
    child_event = matching_pair[1] if matching_pair else (child_events[0] if child_events else None)
    rolled_up = matching_pair is not None
    parent_verified = parent_event is not None
    child_verified = child_event is not None and rolled_up
    verified = parent_verified and child_verified
    missing = []
    if not parent_verified:
        missing.append(f"a live {relationships.get('parent_object') or 'Account'} request")
    if not child_verified:
        missing.append("a live approved related-record request rolled up to the same Account")
    package_setup["verification"] = {
        "verified": verified,
        "parent_verified": parent_verified,
        "child_verified": child_verified,
        "checked_at": datetime.utcnow().isoformat(),
        "parent_audit_id": parent_event.id if parent_event else None,
        "parent_record_name": parent_event.origin_record_name if parent_event else None,
        "child_audit_id": child_event.id if child_event else None,
        "child_record_name": child_event.origin_record_name if child_event else None,
        "message": (
            "Both a parent and related Salesforce request completed the governed pipeline and rolled up together."
            if verified else "Still waiting for " + " and ".join(missing) + "."
        ),
    }
    item.last_tested_at = datetime.utcnow()
    if verified:
        item.last_success_at = datetime.utcnow()
        item.last_error = None
    item.mapping_json = json.dumps(mapping)
    db.commit()
    return _salesforce_package_setup(item)


@router.post("/{connection_id}/package-setup/activate")
def activate_salesforce_package_connection(
    connection_id: int,
    db: Session = Depends(get_db),
):
    """Mark setup live only after selection, relationship approval, and verification."""
    item = _get_connection(db, connection_id)
    if item.platform != "salesforce":
        raise HTTPException(status_code=400, detail="Package setup currently supports Salesforce")
    mapping = _json_object(item.mapping_json)
    package_setup = mapping.get("package_setup")
    if not isinstance(package_setup, dict):
        package_setup = {}
        mapping["package_setup"] = package_setup
    selected = mapping.get("selected_ai_entry_points") or []
    relationships = package_setup.get("relationships") or {}
    verification = package_setup.get("verification") or {}
    missing = []
    if not item.external_tenant_id or not item.instance_url or item.status not in {"connected", "mapping", "active"}:
        missing.append("connect and verify the installed Salesforce org")
    if not item.workspace_id or item.workspace_id.startswith("pending-"):
        missing.append("bind the Salesforce org to one CostPilot workspace")
    if not selected:
        missing.append("choose at least one Agentforce agent or Flow")
    if not relationships.get("approved"):
        missing.append("approve the relationship mapping")
    if not verification.get("parent_verified"):
        missing.append("run and verify one governed request from the parent Account")
    if not verification.get("child_verified"):
        missing.append("run and verify one governed request from an approved related record")
    if missing:
        raise HTTPException(status_code=409, detail="Before going live, " + ", ".join(missing) + ".")
    package_setup["active"] = True
    package_setup["go_live_at"] = datetime.utcnow().isoformat()
    item.status = "active"
    item.last_success_at = datetime.utcnow()
    item.mapping_json = json.dumps(mapping)
    _supersede_salesforce_siblings(item, db)
    db.commit()
    return _salesforce_package_setup(item)


async def _servicenow_refresh_access_token(db: Session, item: IntegrationConnection) -> bool:
    """
    Exchange the stored refresh_token for a new access_token, mirroring
    _salesforce_refresh_access_token above. ServiceNow access tokens are
    also short-lived (typically ~30 min), and nothing previously renewed
    them -- same silent-staleness problem, same fix. Unlike Salesforce's
    PKCE-only flow, ServiceNow's OAuth application requires both
    client_id AND client_secret on every grant, including refresh.
    """
    if not item.refresh_token_encrypted or not item.instance_url:
        return False
    client_id = os.getenv("SERVICENOW_CLIENT_ID")
    client_secret = os.getenv("SERVICENOW_CLIENT_SECRET")
    if not client_id or not client_secret:
        return False
    auth_base = _servicenow_auth_base(item.auth_base_url or item.instance_url)
    refresh_token = _decrypt(item.refresh_token_encrypted)
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{auth_base}/oauth_token.do",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Accept": "application/json"},
        )
    if response.status_code >= 400:
        return False
    try:
        token = response.json()
    except ValueError:
        return False
    access_token = token.get("access_token")
    if not access_token:
        return False
    item.access_token_encrypted = _encrypt(access_token)
    if token.get("refresh_token"):
        item.refresh_token_encrypted = _encrypt(token["refresh_token"])
    item.last_error = None
    db.commit()
    return True


async def _servicenow_table_get(
    item: IntegrationConnection,
    table: str,
    *,
    query: str,
    fields: str,
    limit: int = 500,
    offset: int = 0,
    db: Optional[Session] = None,
    display_value: str = "true",
) -> list[dict]:
    """
    display_value="true" (the default, used by metadata/discovery calls)
    returns reference/choice fields as a flat human-readable string --
    fine for labels, but NOT a stable identifier. Bulk import and outcome
    sync must pass display_value="all" instead, which returns those
    fields as {"display_value": ..., "value": <sys_id or raw value>} so
    callers can use the real sys_id as identity rather than a display
    name -- the same "never use display names as identity" principle
    already applied to Salesforce/WorkItemSourceLink resolution.
    """
    if not item.access_token_encrypted or not item.instance_url:
        raise HTTPException(status_code=409, detail="Connect ServiceNow before discovering metadata")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", table):
        raise HTTPException(status_code=400, detail="Invalid ServiceNow metadata table")

    async def _do_request():
        token = _decrypt(item.access_token_encrypted)
        async with httpx.AsyncClient(timeout=25) as client:
            return await client.get(
                f"{item.instance_url.rstrip('/')}/api/now/table/{table}",
                params={
                    "sysparm_query": query,
                    "sysparm_fields": fields,
                    "sysparm_limit": str(limit),
                    "sysparm_offset": str(offset),
                    "sysparm_display_value": display_value,
                    "sysparm_exclude_reference_link": "true",
                },
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )

    response = await _do_request()
    if response.status_code == 401 and db is not None:
        # Expired access token -- try the refresh token once before giving
        # up, same pattern as _salesforce_get.
        if await _servicenow_refresh_access_token(db, item):
            response = await _do_request()
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
    try:
        payload = response.json()
    except ValueError:
        # A 200 with a non-JSON body happens for real -- most commonly a
        # hibernating Personal Developer Instance returning an HTML
        # "waking up" page instead of the Table API's JSON. Surface this
        # as a clear, retryable error instead of an unhandled 500.
        raise HTTPException(
            status_code=502,
            detail=(
                "ServiceNow returned a non-JSON response -- if this is a "
                "Personal Developer Instance, it may be hibernating and "
                "need a minute to wake up. Try again shortly."
            ),
        )
    return payload.get("result", []) if isinstance(payload, dict) else []


@router.get("/{connection_id}/objects")
async def discover_objects(connection_id: int, db: Session = Depends(get_db)):
    item = _get_connection(db, connection_id)
    if item.platform not in DEEP_INTEGRATION_PLATFORMS:
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
                db=db,
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
    if item.platform not in DEEP_INTEGRATION_PLATFORMS:
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
        child_relationships = recommend_child_relationships(
            payload.get("childRelationships", []),
            parent_object=body.object_name,
        )
    else:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", body.object_name):
            raise HTTPException(status_code=400, detail="Invalid ServiceNow table name")
        dictionary_rows = await _servicenow_table_get(
            item,
            "sys_dictionary",
            query=f"name={body.object_name}^elementISNOTEMPTY^active=true^ORDERBYcolumn_label",
            fields="element,column_label,internal_type,reference,read_only,mandatory",
            limit=1000,
            db=db,
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
            db=db,
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
    previous_discovery = _json_object(item.discovery_json)
    discovery = {
        "object": body.object_name,
        "object_label": object_label,
        "fields": fields,
        "recommendations": recommendations,
        "child_relationships": child_relationships,
        "discovered_at": datetime.utcnow().isoformat(),
    }
    if isinstance(previous_discovery.get("context_monitor"), dict):
        discovery["context_monitor"] = previous_discovery["context_monitor"]
    item.selected_object = body.object_name
    item.discovery_json = json.dumps(discovery)
    item.status = "mapping"
    item.last_success_at = datetime.utcnow()
    item.last_error = None
    db.commit()
    return discovery


@router.put("/{connection_id}/tracked-objects")
def set_tracked_objects(connection_id: int, body: TrackedObjectsUpdate, db: Session = Depends(get_db)):
    """
    Records which additional objects (beyond the single primary
    `selected_object` that import actually runs against today) the admin
    wants CostPilot to track. This is intent, not import -- multi-object
    import would mean looping the existing single-object import logic per
    tracked object, which isn't wired up yet. Storing the opt-in now means
    that intent survives until the import loop catches up, rather than
    forcing a UI to pretend only one object can ever matter.
    """
    item = _get_connection(db, connection_id)
    cleaned = sorted({name.strip() for name in body.objects if name.strip()})
    item.tracked_objects_json = json.dumps(cleaned)
    item.updated_at = datetime.utcnow()
    db.commit()
    return {"connection_id": item.id, "tracked_objects": cleaned}


@router.get("/{connection_id}/context-discovery")
def get_context_discovery(connection_id: int, db: Session = Depends(get_db)):
    item = _get_connection(db, connection_id)
    discovery = _json_object(item.discovery_json)
    monitor = discovery.get("context_monitor") or {}
    return {
        "connection_id": item.id,
        "platform": item.platform,
        "configured": bool(monitor.get("baseline")),
        "last_scan_at": monitor.get("last_scan_at"),
        "pending_changes": [
            change for change in monitor.get("pending_changes", [])
            if change.get("status") == "pending"
        ],
        "history": monitor.get("history", [])[-25:],
    }


@router.post("/{connection_id}/context-discovery/scan")
async def scan_context_changes(connection_id: int, db: Session = Depends(get_db)):
    item = _get_connection(db, connection_id)
    if item.platform != "salesforce":
        raise HTTPException(status_code=501, detail="Continuous context discovery currently supports Salesforce")
    mapping = _json_object(item.mapping_json)
    parent_object = str(mapping.get("parent_object") or item.selected_object or "").strip()
    if not parent_object:
        raise HTTPException(status_code=409, detail="Approve a parent object before scanning for changes")

    objects_payload = await _salesforce_get(item, "sobjects")
    objects = [
        {"name": obj.get("name"), "label": obj.get("label") or obj.get("name"), "custom": obj.get("custom", False)}
        for obj in objects_payload.get("sobjects", [])
        if obj.get("name") and obj.get("queryable") and not obj.get("deprecatedAndHidden")
    ]
    describe = await _salesforce_get(item, f"sobjects/{quote(parent_object, safe='')}/describe")
    relationships = recommend_child_relationships(
        describe.get("childRelationships", []),
        parent_object=parent_object,
    )
    current = _build_context_snapshot(objects, parent_object, relationships)
    discovery = _json_object(item.discovery_json)
    monitor = discovery.get("context_monitor") if isinstance(discovery.get("context_monitor"), dict) else {}
    previous = monitor.get("baseline") if isinstance(monitor.get("baseline"), dict) else None
    existing = monitor.get("pending_changes") if isinstance(monitor.get("pending_changes"), list) else []
    existing_ids = {change.get("id") for change in existing}
    additions = _new_context_changes(previous, current) if previous else []
    existing.extend(change for change in additions if change.get("id") not in existing_ids)
    monitor.update({
        "schema_version": 1,
        "baseline": current,
        "last_scan_at": current["captured_at"],
        "pending_changes": existing,
    })
    discovery["context_monitor"] = monitor
    item.discovery_json = json.dumps(discovery)
    item.last_success_at = datetime.utcnow()
    item.last_error = None
    db.commit()
    return {
        "connection_id": item.id,
        "configured": True,
        "baseline_created": previous is None,
        "last_scan_at": monitor["last_scan_at"],
        "new_change_count": len(additions),
        "pending_changes": [change for change in existing if change.get("status") == "pending"],
    }


@router.post("/{connection_id}/context-discovery/changes/{change_id}")
def decide_context_change(
    connection_id: int,
    change_id: str,
    body: ContextChangeDecision,
    db: Session = Depends(get_db),
):
    item = _get_connection(db, connection_id)
    discovery = _json_object(item.discovery_json)
    monitor = discovery.get("context_monitor") if isinstance(discovery.get("context_monitor"), dict) else {}
    changes = monitor.get("pending_changes") if isinstance(monitor.get("pending_changes"), list) else []
    change = next((value for value in changes if value.get("id") == change_id), None)
    if not change:
        raise HTTPException(status_code=404, detail="Discovered change not found")
    if change.get("status") != "pending":
        raise HTTPException(status_code=409, detail="This change has already been reviewed")

    behavior = body.behavior or change.get("suggested_behavior") or "separate"
    change["status"] = "approved" if body.decision == "approve" else "ignored"
    change["decision_at"] = datetime.utcnow().isoformat()
    change["behavior"] = behavior if body.decision == "approve" else "ignore"
    mapping = _json_object(item.mapping_json)
    if body.decision == "approve" and change.get("kind") == "relationship_added":
        children = mapping.get("children") if isinstance(mapping.get("children"), list) else []
        key = (change.get("object"), change.get("parent_field"))
        children = [child for child in children if (child.get("object"), child.get("parent_field")) != key]
        children.append({
            "object": change.get("object"),
            "label": change.get("label") or change.get("object"),
            "parent_field": change.get("parent_field"),
            "relationship_name": change.get("relationship_name"),
            "behavior": behavior,
        })
        mapping["children"] = children
    elif body.decision == "approve" and change.get("kind") == "object_added":
        approved = mapping.get("approved_objects") if isinstance(mapping.get("approved_objects"), list) else []
        approved = [value for value in approved if value.get("object") != change.get("object")]
        approved.append({"object": change.get("object"), "label": change.get("label"), "behavior": behavior})
        mapping["approved_objects"] = approved
    history = monitor.get("history") if isinstance(monitor.get("history"), list) else []
    history.append(dict(change))
    monitor["history"] = history[-100:]
    discovery["context_monitor"] = monitor
    item.discovery_json = json.dumps(discovery)
    item.mapping_json = json.dumps(mapping)
    item.updated_at = datetime.utcnow()
    db.commit()
    return {"change": change, "connection": _public_connection(item)}


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
    item.status = "mapping" if item.platform == "salesforce" else "active"
    item.last_tested_at = datetime.utcnow()
    item.last_success_at = datetime.utcnow()
    item.last_error = None
    db.commit()
    db.refresh(item)
    return _public_connection(item)


# ── Outcome sync (AI Event -> Work Item -> Outcome) ─────────────────────────
#
# Business-outcome enrichment, now covering two Salesforce object types
# (Opportunity, Case) via the shared _sync_salesforce_object_outcomes loop
# below -- Case was added specifically to prove the pattern generalizes,
# not just Opportunity's. Still incremental sync only (no webhooks yet --
# see core/outcome_adapters/salesforce_opportunity.py's module docstring).
# Triggered by an external process (scripts/sync_all_salesforce_outcomes.py,
# run on a schedule) rather than a new in-app background-job system.

async def _sync_object_outcomes(
    db: Session,
    item: IntegrationConnection,
    *,
    source_platform: str,
    source_record_type: str,
    fetch_batch,
    map_record,
) -> dict:
    """
    Shared sync loop for any connected-system object with an outcome
    adapter -- Salesforce Opportunity/Case and ServiceNow incident/Case
    all call this with their own batch-fetcher and field-mapper. Adding a
    fourth object type means writing a fourth adapter module, not
    touching this loop. See core/outcome_adapters/ for the adapters
    themselves. fetch_batch(batch_ids) -> (records, error) hides the
    platform-specific bounded-query shape (SOQL "WHERE Id IN (...)" vs
    ServiceNow's "sys_idIN..." encoded query).
    """
    work_items = (
        db.query(WorkItem)
        .filter(
            WorkItem.workspace_id == item.workspace_id,
            WorkItem.source_platform == source_platform,
            WorkItem.source_record_type == source_record_type,
            WorkItem.source_record_id.isnot(None),
        )
        .all()
    )
    if not work_items:
        return {"checked": 0, "updated": 0, "unchanged": 0, "errors": []}

    by_record_id = {wi.source_record_id: wi for wi in work_items}
    errors: list[str] = []
    updated = 0
    unchanged = 0

    # Batch in chunks of 200 -- SOQL/encoded-query IN() lists and URL
    # length both have practical limits; this also bounds a single sync
    # run's blast radius.
    record_ids = list(by_record_id.keys())
    for start in range(0, len(record_ids), 200):
        batch = record_ids[start:start + 200]
        try:
            records, error = await fetch_batch(batch)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if error:
            errors.append(error)
            continue

        now = datetime.utcnow()
        for record in records:
            canonical = map_record(record)
            work_item = by_record_id.get(canonical["external_id"])
            if not work_item:
                continue

            existing = (
                db.query(WorkItemOutcome)
                .filter_by(work_item_id=work_item.id)
                .first()
            )
            changed = existing is None or (
                existing.outcome_status != canonical["outcome_status"]
                or existing.outcome_value != canonical["outcome_value"]
                or existing.outcome_date != canonical["outcome_date"]
                or existing.outcome_success != canonical["outcome_success"]
                or existing.is_closed != canonical["is_closed"]
            )
            if not changed:
                existing.last_synced_at = now
                unchanged += 1
                continue

            if existing is None:
                existing = WorkItemOutcome(
                    work_item_id=work_item.id,
                    workspace_id=item.workspace_id,
                )
                db.add(existing)
            existing.outcome_status = canonical["outcome_status"]
            existing.outcome_value = canonical["outcome_value"]
            existing.outcome_date = canonical["outcome_date"]
            existing.outcome_success = canonical["outcome_success"]
            existing.is_closed = canonical["is_closed"]
            existing.owner = canonical["owner"]
            existing.source_system = canonical["source_system"]
            existing.source_object = canonical["source_object"]
            existing.external_id = canonical["external_id"]
            existing.source_modified_at = canonical["source_modified_at"]
            existing.last_synced_at = now
            existing.retrieval_method = "sync"

            db.add(WorkItemOutcomeEvent(
                work_item_id=work_item.id,
                workspace_id=item.workspace_id,
                outcome_status=canonical["outcome_status"],
                outcome_value=canonical["outcome_value"],
                outcome_date=canonical["outcome_date"],
                outcome_success=canonical["outcome_success"],
                is_closed=canonical["is_closed"],
                retrieval_method="sync",
                recorded_at=now,
            ))
            updated += 1

    db.commit()
    return {
        "checked": len(record_ids),
        "updated": updated,
        "unchanged": unchanged,
        "errors": errors,
    }


async def _sync_salesforce_opportunity_outcomes(db: Session, item: IntegrationConnection) -> dict:
    from core.outcome_adapters.salesforce_opportunity import (
        build_opportunity_query,
        map_salesforce_opportunity_to_canonical_outcome,
    )
    async def fetch_batch(batch):
        return await _salesforce_try_query(item, build_opportunity_query(batch), db=db)
    return await _sync_object_outcomes(
        db, item,
        source_platform="Salesforce",
        source_record_type="Opportunity",
        fetch_batch=fetch_batch,
        map_record=map_salesforce_opportunity_to_canonical_outcome,
    )


async def _sync_salesforce_case_outcomes(db: Session, item: IntegrationConnection) -> dict:
    from core.outcome_adapters.salesforce_case import (
        build_case_query,
        map_salesforce_case_to_canonical_outcome,
    )
    async def fetch_batch(batch):
        return await _salesforce_try_query(item, build_case_query(batch), db=db)
    return await _sync_object_outcomes(
        db, item,
        source_platform="Salesforce",
        source_record_type="Case",
        fetch_batch=fetch_batch,
        map_record=map_salesforce_case_to_canonical_outcome,
    )


async def _servicenow_try_query(
    item: IntegrationConnection, table: str, *, sys_ids: list[str], fields: str, db: Optional[Session] = None,
) -> tuple[list[dict], Optional[str]]:
    """Bounded lookup of known sys_ids -- the ServiceNow analog of
    _salesforce_try_query's "WHERE Id IN (...)" batch fetch, used by
    outcome sync (not the unbounded _servicenow_query_all used by bulk
    import)."""
    safe_ids = [sid for sid in sys_ids if re.fullmatch(r"[0-9a-fA-F]{32}", sid)]
    if len(safe_ids) != len(sys_ids):
        return [], "One or more sys_ids are not valid ServiceNow record ids"
    query = f"sys_idIN{','.join(safe_ids)}"
    try:
        records = await _servicenow_table_get(
            item, table, query=query, fields=fields, limit=len(safe_ids) or 1,
            db=db, display_value="all",
        )
        return records, None
    except HTTPException as exc:
        return [], str(exc.detail)


async def _sync_servicenow_incident_outcomes(db: Session, item: IntegrationConnection) -> dict:
    from core.outcome_adapters.servicenow_incident import (
        SERVICENOW_INCIDENT_TABLE,
        SERVICENOW_INCIDENT_OUTCOME_FIELDS,
        map_servicenow_incident_to_canonical_outcome,
    )
    async def fetch_batch(batch):
        return await _servicenow_try_query(
            item, SERVICENOW_INCIDENT_TABLE, sys_ids=batch,
            fields=",".join(SERVICENOW_INCIDENT_OUTCOME_FIELDS), db=db,
        )
    return await _sync_object_outcomes(
        db, item,
        source_platform="ServiceNow",
        source_record_type="incident",
        fetch_batch=fetch_batch,
        map_record=map_servicenow_incident_to_canonical_outcome,
    )


async def _sync_servicenow_case_outcomes(db: Session, item: IntegrationConnection) -> dict:
    from core.outcome_adapters.servicenow_case import (
        SERVICENOW_CASE_TABLE,
        SERVICENOW_CASE_OUTCOME_FIELDS,
        map_servicenow_case_to_canonical_outcome,
    )
    async def fetch_batch(batch):
        return await _servicenow_try_query(
            item, SERVICENOW_CASE_TABLE, sys_ids=batch,
            fields=",".join(SERVICENOW_CASE_OUTCOME_FIELDS), db=db,
        )
    return await _sync_object_outcomes(
        db, item,
        source_platform="ServiceNow",
        source_record_type="sn_customerservice_case",
        fetch_batch=fetch_batch,
        map_record=map_servicenow_case_to_canonical_outcome,
    )


def _merge_sync_results(*results: dict) -> dict:
    merged = {"checked": 0, "updated": 0, "unchanged": 0, "errors": []}
    for result in results:
        merged["checked"] += result["checked"]
        merged["updated"] += result["updated"]
        merged["unchanged"] += result["unchanged"]
        merged["errors"] += result["errors"]
    return merged


async def _sync_connection_outcomes(db: Session, item: IntegrationConnection) -> dict:
    """
    Platform dispatch shared by the manual /sync-outcomes endpoint and the
    automatic background sweep (see run_outcome_sync_sweep in
    core/outcome_sync_scheduler.py) -- one place decides which adapters run
    for a given platform, so the two callers can never drift apart on what
    "syncing this connection" actually means.
    """
    if item.platform == "salesforce":
        result = _merge_sync_results(
            await _sync_salesforce_opportunity_outcomes(db, item),
            await _sync_salesforce_case_outcomes(db, item),
        )
    elif item.platform == "servicenow":
        result = _merge_sync_results(
            await _sync_servicenow_incident_outcomes(db, item),
            await _sync_servicenow_case_outcomes(db, item),
        )
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Outcome sync is not available for platform '{item.platform}'",
        )
    # Only a real success should move last_success_at -- it was previously
    # set unconditionally, which made a fully-failed sync (e.g. every batch
    # 401ing) look like it had just succeeded when the connection's health
    # was checked, masking exactly the kind of stale-token problem this was
    # meant to help catch.
    if not result["errors"]:
        item.last_success_at = datetime.utcnow()
    item.last_outcome_sync_at = datetime.utcnow()
    db.commit()
    return result


@router.post("/{connection_id}/sync-outcomes")
async def sync_outcomes(connection_id: int, db: Session = Depends(get_db)):
    """
    Pull current status/value/close-date for every WorkItem this workspace
    has already linked to a Salesforce Opportunity/Case or ServiceNow
    incident/Case, and update WorkItemOutcome (+ append
    WorkItemOutcomeEvent history on change).

    The connected system remains the system of record -- this only ever
    reads.
    """
    item = _get_connection(db, connection_id)
    _require_connected(item)
    return await _sync_connection_outcomes(db, item)


# ── Bulk import (Connect -> Discover -> Import) ─────────────────────────────
#
# Before this, the only way a WorkItem+WorkItemSourceLink pair existed was
# a human calling the API by hand for one record at a time (or an
# Agentforce action reactively creating one, which the account-level
# fallback in routes_agentforce.py could get wrong when no source link
# existed yet). A real company has hundreds or thousands of Opportunities
# -- that doesn't scale to "someone manually creates each one."
#
# This function deliberately knows nothing about Salesforce fields --
# everything vendor-specific comes in through build_all_query/
# map_to_work_item/map_to_outcome, the same three-function shape
# _sync_salesforce_object_outcomes already uses for the outcome side. A
# second platform (Jira, HubSpot) would mean writing a sibling adapter
# with this same shape, not touching this function -- the intended test
# from the universal Business Context design: "would connecting a new
# platform require changing this code?" should be no.

async def _import_work_items(
    db: Session,
    item: IntegrationConnection,
    *,
    source_platform: str,
    source_record_type: str,
    context_type: str,
    external_id_prefix: str,
    fetch_records,
    map_to_work_item,
    map_to_outcome,
    dry_run: bool = False,
) -> dict:
    """
    Platform-agnostic bulk-import core, extracted from the Salesforce-only
    version of this function. Everything platform-specific is pushed into
    the caller: how records are fetched (fetch_records -- SOQL cursor
    pagination for Salesforce, sysparm_offset pagination for ServiceNow,
    see _salesforce_query_all vs _servicenow_query_all), and how a raw
    record becomes work-item/outcome fields (map_to_work_item/
    map_to_outcome, the same duck-typed adapter shape core/outcome_adapters
    already uses). Everything below -- identity via WorkItemSourceLink's
    natural key, the claimed_work_item_ids self-heal, account upsert,
    outcome-change detection, dry_run rollback -- is genuinely universal
    and was already written that way; it just had "Salesforce" hardcoded
    in a few string literals instead of taking them as parameters.
    """
    records, error = await fetch_records()
    if error:
        return {"discovered": 0, "created": 0, "updated": 0, "healed": 0, "errors": [error]}

    created = 0
    updated = 0
    healed = 0
    errors: list[str] = []
    now = datetime.utcnow()
    # Work items already claimed by a specific source record THIS run.
    # Needed because of a real data-corruption pattern found in production:
    # before bulk import existed, Agentforce's reactive fallback (see
    # routes_agentforce.py _resolve_or_create_project) could point several
    # genuinely different Opportunities' WorkItemSourceLink rows at the
    # SAME generic account-level WorkItem, since it picked "the account's
    # oldest work item" whenever no link existed yet. Reusing that shared
    # link for more than one record here would either silently merge
    # unrelated deals or (as found live) crash on WorkItemOutcome's
    # one-row-per-work-item constraint when two records in the same import
    # both try to claim it. Self-heal instead: the first record to reach an
    # already-claimed link keeps it; every subsequent one gets its own
    # fresh, correctly 1:1 WorkItem, and its link is repointed.
    claimed_work_item_ids: set[int] = set()

    for record in records:
        try:
            wi_fields = map_to_work_item(record)
        except Exception as exc:
            errors.append(str(exc))
            continue

        account = None
        account_external_id = wi_fields.get("account_external_id")
        if account_external_id:
            account = (
                db.query(WorkAccount)
                .filter_by(workspace_id=item.workspace_id, external_id=account_external_id)
                .first()
            )
            if not account:
                account = WorkAccount(
                    external_id=account_external_id,
                    name=wi_fields.get("account_name") or account_external_id,
                    workspace_id=item.workspace_id,
                )
                db.add(account)
                db.flush()
            else:
                # An existing account's name was previously never refreshed
                # on re-sync -- a rename in Salesforce (this is the one
                # field every Opportunity/Case record actually carries,
                # via the joined Account.Name) would silently never reach
                # CostPilot once the account row already existed. Only
                # `name` is touched here -- status/merged_into_work_account_id
                # are CostPilot-owned merge state (see the account-merge
                # endpoints), not something Salesforce sync should ever
                # overwrite.
                incoming_name = wi_fields.get("account_name")
                if incoming_name and account.name != incoming_name:
                    account.name = incoming_name

        # Idempotent (section 12 of the design brief): identity is the
        # source link (workspace + platform + source record id), not a
        # freshly-generated id, so importing the same Opportunity twice
        # updates the existing WorkItem instead of duplicating it.
        link = (
            db.query(WorkItemSourceLink)
            .filter_by(
                workspace_id=item.workspace_id,
                source_platform=source_platform,
                source_record_id=wi_fields["source_record_id"],
            )
            .first()
        )
        work_item = db.query(WorkItem).filter_by(id=link.work_item_id).first() if link else None
        if work_item is not None and work_item.id in claimed_work_item_ids:
            # This link points at a WorkItem a different source record
            # already claimed this run -- it's stale/shared, not this
            # record's real WorkItem. Force creating this record's own.
            work_item = None
            healed += 1

        context_template = f"{source_platform.lower()}_{context_type}"

        if work_item is None:
            work_item = WorkItem(
                external_id=f"{external_id_prefix}-{wi_fields['source_record_id']}",
                name=wi_fields["name"],
                account_id=account.id if account else None,
                context_type=context_type,
                context_template=context_template,
                source_platform=source_platform,
                source_record_type=source_record_type,
                source_record_id=wi_fields["source_record_id"],
                workspace_id=item.workspace_id,
            )
            db.add(work_item)
            db.flush()
            if link is not None:
                # Repoint the existing (previously mis-shared) link rather
                # than create a second link row for the same source record.
                link.work_item_id = work_item.id
                link.source_record_name = wi_fields["name"]
                link.account_external_id = account_external_id
            else:
                db.add(WorkItemSourceLink(
                    work_item_id=work_item.id,
                    workspace_id=item.workspace_id,
                    source_platform=source_platform,
                    source_record_type=source_record_type,
                    source_record_id=wi_fields["source_record_id"],
                    source_record_name=wi_fields["name"],
                    account_external_id=account_external_id,
                    is_primary=True,
                ))
            created += 1
        else:
            work_item.name = wi_fields["name"]
            if account and work_item.account_id != account.id:
                work_item.account_id = account.id
            updated += 1
        claimed_work_item_ids.add(work_item.id)

        # Seed/refresh the outcome in the same pass -- the record is
        # already in hand, so there's no reason to make a second API call
        # just to populate day-one outcome data for a newly-imported item.
        canonical = map_to_outcome(record)
        outcome = db.query(WorkItemOutcome).filter_by(work_item_id=work_item.id).first()
        changed = outcome is None or (
            outcome.outcome_status != canonical["outcome_status"]
            or outcome.outcome_value != canonical["outcome_value"]
            or outcome.outcome_date != canonical["outcome_date"]
            or outcome.outcome_success != canonical["outcome_success"]
            or outcome.is_closed != canonical["is_closed"]
        )
        if outcome is None:
            outcome = WorkItemOutcome(work_item_id=work_item.id, workspace_id=item.workspace_id)
            db.add(outcome)
        if changed:
            outcome.outcome_status = canonical["outcome_status"]
            outcome.outcome_value = canonical["outcome_value"]
            outcome.outcome_date = canonical["outcome_date"]
            outcome.outcome_success = canonical["outcome_success"]
            outcome.is_closed = canonical["is_closed"]
            outcome.owner = canonical["owner"]
            outcome.source_system = canonical["source_system"]
            outcome.source_object = canonical["source_object"]
            outcome.external_id = canonical["external_id"]
            outcome.source_modified_at = canonical["source_modified_at"]
            outcome.retrieval_method = "import"
            db.add(WorkItemOutcomeEvent(
                work_item_id=work_item.id,
                workspace_id=item.workspace_id,
                outcome_status=canonical["outcome_status"],
                outcome_value=canonical["outcome_value"],
                outcome_date=canonical["outcome_date"],
                outcome_success=canonical["outcome_success"],
                is_closed=canonical["is_closed"],
                retrieval_method="import",
                recorded_at=now,
            ))
        outcome.last_synced_at = now

    # Preview mode: everything above (including the id-generating flushes
    # each WorkItem/account create needs) still ran for real, so the counts
    # reflect true would-be create/update/heal outcomes -- just never
    # persisted. Reuses the exact same logic as a real import rather than a
    # parallel counting path that could drift out of sync with it.
    if dry_run:
        db.rollback()
    else:
        db.commit()
    return {
        "discovered": len(records), "created": created, "updated": updated,
        "healed": healed, "errors": errors, "dry_run": dry_run,
    }


@router.post("/{connection_id}/import-work-items")
async def import_work_items(
    connection_id: int,
    object_type: str = Query(..., description="Opportunity, Case, incident, or sn_customerservice_case"),
    dry_run: bool = False,
    db: Session = Depends(get_db),
):
    """
    Bulk-discover and import every matching business record from a
    connected Salesforce or ServiceNow org as CostPilot WorkItems, with
    outcome data seeded in the same pass. Safe to re-run (idempotent --
    updates existing WorkItems by source link rather than duplicating
    them). Pass dry_run=true to preview counts before committing.
    """
    item = _get_connection(db, connection_id)
    _require_connected(item)

    if item.platform == "salesforce" and object_type == "Opportunity":
        from core.outcome_adapters.salesforce_opportunity import (
            build_all_opportunities_query,
            map_salesforce_opportunity_to_work_item_fields,
            map_salesforce_opportunity_to_canonical_outcome,
        )
        result = await _import_work_items(
            db, item,
            source_platform="Salesforce",
            source_record_type="Opportunity",
            context_type="opportunity",
            external_id_prefix="SF-OPPORTUNITY",
            fetch_records=lambda: _salesforce_query_all(item, build_all_opportunities_query(), db=db),
            map_to_work_item=map_salesforce_opportunity_to_work_item_fields,
            map_to_outcome=map_salesforce_opportunity_to_canonical_outcome,
            dry_run=dry_run,
        )
    elif item.platform == "salesforce" and object_type == "Case":
        from core.outcome_adapters.salesforce_case import (
            build_all_cases_query,
            map_salesforce_case_to_work_item_fields,
            map_salesforce_case_to_canonical_outcome,
        )
        result = await _import_work_items(
            db, item,
            source_platform="Salesforce",
            source_record_type="Case",
            context_type="case",
            external_id_prefix="SF-CASE",
            fetch_records=lambda: _salesforce_query_all(item, build_all_cases_query(), db=db),
            map_to_work_item=map_salesforce_case_to_work_item_fields,
            map_to_outcome=map_salesforce_case_to_canonical_outcome,
            dry_run=dry_run,
        )
    elif item.platform == "servicenow" and object_type == "incident":
        from core.outcome_adapters.servicenow_incident import (
            SERVICENOW_INCIDENT_TABLE,
            SERVICENOW_INCIDENT_OUTCOME_FIELDS,
            build_all_incidents_query,
            map_servicenow_incident_to_work_item_fields,
            map_servicenow_incident_to_canonical_outcome,
        )
        result = await _import_work_items(
            db, item,
            source_platform="ServiceNow",
            source_record_type="incident",
            context_type="case",
            external_id_prefix="SN-INCIDENT",
            fetch_records=lambda: _servicenow_query_all(
                item, SERVICENOW_INCIDENT_TABLE,
                query=build_all_incidents_query(),
                fields=",".join(SERVICENOW_INCIDENT_OUTCOME_FIELDS),
                db=db,
            ),
            map_to_work_item=map_servicenow_incident_to_work_item_fields,
            map_to_outcome=map_servicenow_incident_to_canonical_outcome,
            dry_run=dry_run,
        )
    elif item.platform == "servicenow" and object_type == "sn_customerservice_case":
        from core.outcome_adapters.servicenow_case import (
            SERVICENOW_CASE_TABLE,
            SERVICENOW_CASE_OUTCOME_FIELDS,
            build_all_cases_query,
            map_servicenow_case_to_work_item_fields,
            map_servicenow_case_to_canonical_outcome,
        )
        result = await _import_work_items(
            db, item,
            source_platform="ServiceNow",
            source_record_type="sn_customerservice_case",
            context_type="case",
            external_id_prefix="SN-CASE",
            fetch_records=lambda: _servicenow_query_all(
                item, SERVICENOW_CASE_TABLE,
                query=build_all_cases_query(),
                fields=",".join(SERVICENOW_CASE_OUTCOME_FIELDS),
                db=db,
            ),
            map_to_work_item=map_servicenow_case_to_work_item_fields,
            map_to_outcome=map_servicenow_case_to_canonical_outcome,
            dry_run=dry_run,
        )
    else:
        raise HTTPException(
            status_code=400,
            detail=f"object_type '{object_type}' is not importable for platform '{item.platform}'",
        )

    return result
