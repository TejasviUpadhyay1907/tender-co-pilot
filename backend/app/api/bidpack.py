"""Bid-pack checklist endpoints (/api/bidpack)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import BidPackItem, Tender, get_db

router = APIRouter(prefix="/api/bidpack", tags=["bidpack"])


class BidPackItemIn(BaseModel):
    item: str
    required: bool = True
    status: str = "pending"


class BidPackUpdate(BaseModel):
    items: list[BidPackItemIn]


class BidPackItemOut(BaseModel):
    id: int
    tender_id: int
    item: str
    required: bool
    status: str

    class Config:
        from_attributes = True


def _require_tender(tender_id: int, db: Session) -> Tender:
    tender = db.get(Tender, tender_id)
    if tender is None:
        raise HTTPException(status_code=404, detail="Tender not found")
    return tender


@router.get("/{tender_id}", response_model=list[BidPackItemOut])
def get_bidpack(tender_id: int, db: Session = Depends(get_db)):
    _require_tender(tender_id, db)
    return (
        db.query(BidPackItem)
        .filter(BidPackItem.tender_id == tender_id)
        .order_by(BidPackItem.id.asc())
        .all()
    )


@router.put("/{tender_id}", response_model=list[BidPackItemOut])
def put_bidpack(tender_id: int, payload: BidPackUpdate, db: Session = Depends(get_db)):
    """Upsert checklist items by item text; returns the full checklist."""
    _require_tender(tender_id, db)
    for it in payload.items:
        existing = (
            db.query(BidPackItem)
            .filter(BidPackItem.tender_id == tender_id, BidPackItem.item == it.item)
            .first()
        )
        if existing is not None:
            existing.status = it.status
            existing.required = it.required
        else:
            db.add(
                BidPackItem(
                    tender_id=tender_id,
                    item=it.item,
                    required=it.required,
                    status=it.status,
                )
            )
    db.commit()
    return (
        db.query(BidPackItem)
        .filter(BidPackItem.tender_id == tender_id)
        .order_by(BidPackItem.id.asc())
        .all()
    )


@router.delete(
    "/{tender_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_bidpack_item(tender_id: int, item_id: int, db: Session = Depends(get_db)):
    item = (
        db.query(BidPackItem)
        .filter(BidPackItem.id == item_id, BidPackItem.tender_id == tender_id)
        .first()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Bid-pack item not found")
    db.delete(item)
    db.commit()
    return None
