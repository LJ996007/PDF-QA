"""Shared audit profile routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.schemas import AuditProfile, AuditProfileCreateRequest, AuditProfileUpdateRequest
from app.services.audit_profile_service import audit_profile_service

router = APIRouter()


@router.get("", response_model=list[AuditProfile])
async def list_audit_profiles():
    return audit_profile_service.list_profiles()


@router.post("", response_model=AuditProfile)
async def create_audit_profile(request: AuditProfileCreateRequest):
    try:
        return audit_profile_service.create_profile(request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/{profile_id}", response_model=AuditProfile)
async def update_audit_profile(profile_id: str, request: AuditProfileUpdateRequest):
    try:
        return audit_profile_service.update_profile(profile_id, request.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="审核模板不存在。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{profile_id}")
async def delete_audit_profile(profile_id: str):
    try:
        audit_profile_service.delete_profile(profile_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="审核模板不存在。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "deleted"}
