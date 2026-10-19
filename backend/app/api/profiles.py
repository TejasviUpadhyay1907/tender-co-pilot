"""Company profile CRUD (/api/profiles)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import CompanyProfile, get_db

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


class ProfileBase(BaseModel):
    name: str
    sector: str = ""
    registration_type: str = ""
    udyam_registered: bool = False
    dpiit_startup: bool = False
    turnover_avg_3y: Optional[float] = None
    turnover_yearly: Optional[dict] = None
    experience_json: Optional[dict] = None
    local_content_pct: Optional[float] = None
    certifications_json: Optional[list] = None
    blacklisted: bool = False
    notes: Optional[str] = None
    # Friendly write-aliases used by the SPA form; mapped to the
    # storage columns by _apply_aliases() before persisting.
    certifications: Optional[list] = None
    experience: Optional[list] = None


class ProfileCreate(ProfileBase):
    pass


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    sector: Optional[str] = None
    registration_type: Optional[str] = None
    udyam_registered: Optional[bool] = None
    dpiit_startup: Optional[bool] = None
    turnover_avg_3y: Optional[float] = None
    turnover_yearly: Optional[dict] = None
    experience_json: Optional[dict] = None
    local_content_pct: Optional[float] = None
    certifications_json: Optional[list] = None
    blacklisted: Optional[bool] = None
    notes: Optional[str] = None
    certifications: Optional[list] = None
    experience: Optional[list] = None


def _apply_aliases(data: dict) -> dict:
    """Map the SPA's friendly write-fields to storage columns.

    - ``certifications`` (list of names) -> ``certifications_json``
    - ``experience`` (list of {work, value_rs, year}) ->
      ``experience_json`` = {"items": [...], "max_single_value": N}
    """
    data = dict(data)
    certs = data.pop("certifications", None)
    if certs is not None:
        data["certifications_json"] = certs
    exp = data.pop("experience", None)
    if exp is not None:
        items = exp if isinstance(exp, list) else []
        vals = [
            i.get("value_rs")
            for i in items
            if isinstance(i, dict) and isinstance(i.get("value_rs"), (int, float))
        ]
        data["experience_json"] = {
            "items": items,
            "max_single_value": max(vals) if vals else None,
        }
    return data


def _enrich_out(profile: CompanyProfile) -> dict:
    """ProfileOut dict plus the friendly read-aliases the SPA form expects."""
    d = ProfileOut.model_validate(profile).model_dump(mode="json")
    d["certifications"] = profile.certifications_json or []
    exp = profile.experience_json
    d["experience"] = exp.get("items", []) if isinstance(exp, dict) else []
    return d


class ProfileOut(ProfileBase):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


@router.get("")
def list_profiles(db: Session = Depends(get_db)):
    rows = db.query(CompanyProfile).order_by(CompanyProfile.id.desc()).all()
    return [_enrich_out(p) for p in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_profile(payload: ProfileCreate, db: Session = Depends(get_db)):
    data = _apply_aliases(payload.model_dump())
    profile = CompanyProfile(**data)
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return _enrich_out(profile)


@router.get("/{profile_id}")
def get_profile(profile_id: int, db: Session = Depends(get_db)):
    profile = db.get(CompanyProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return _enrich_out(profile)


@router.put("/{profile_id}")
def update_profile(profile_id: int, payload: ProfileUpdate, db: Session = Depends(get_db)):
    profile = db.get(CompanyProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    data = _apply_aliases(payload.model_dump(exclude_unset=True))
    for field, value in data.items():
        setattr(profile, field, value)
    db.commit()
    db.refresh(profile)
    return _enrich_out(profile)


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile(profile_id: int, db: Session = Depends(get_db)):
    profile = db.get(CompanyProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    db.delete(profile)
    db.commit()
    return None
