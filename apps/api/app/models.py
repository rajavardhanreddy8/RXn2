from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class TargetResolveRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    query_type: Literal["auto", "name", "smiles", "inchi_key"] = "auto"


class RouteConstraints(BaseModel):
    max_steps: int = Field(default=6, ge=1, le=12)
    max_routes: int = Field(default=10, ge=1, le=10)
    excluded_compound_ids: list[str] = Field(default_factory=list)
    excluded_hazard_codes: list[str] = Field(default_factory=list)


class RouteGenerateRequest(BaseModel):
    compound_id: str | None = None
    query: str | None = None
    target_mass_g: float = Field(default=1000, gt=0, le=10_000_000)
    base_currency: str = Field(default="USD", min_length=3, max_length=3)
    fx_date: str | None = None
    constraints: RouteConstraints = Field(default_factory=RouteConstraints)

    @model_validator(mode="after")
    def target_present(self):
        if not self.compound_id and not self.query:
            raise ValueError("compound_id or query is required")
        self.base_currency = self.base_currency.upper()
        return self


class RouteCompareRequest(BaseModel):
    route_ids: list[str] = Field(min_length=2, max_length=10)


class PriceQuoteInput(BaseModel):
    quote_id: str
    compound_id: str
    supplier_id: str
    supplier_name: str
    source_url: str | None = None
    observed_at: str
    currency: str = Field(min_length=3, max_length=3)
    geography: str | None = None
    purity_percent: float | None = Field(default=None, gt=0, le=100)
    pack_size_value: float = Field(gt=0)
    pack_size_unit: Literal["mg", "g", "kg"]
    available_quantity_value: float | None = Field(default=None, ge=0)
    available_quantity_unit: Literal["mg", "g", "kg"] | None = None
    price: float = Field(ge=0)
    review_status: Literal["accepted", "needs_review", "unreviewed"] = "needs_review"


class PriceImportRequest(BaseModel):
    quotes: list[PriceQuoteInput] = Field(min_length=1, max_length=5000)


class QroqExtractionRequest(BaseModel):
    source_text: str = Field(min_length=20, max_length=200_000)
    source_url: str | None = None
    model: str = "llama-3.3-70b-versatile"
    data_classification: Literal["public", "proprietary"] = "public"
    allow_external_processing: bool = False

    @model_validator(mode="after")
    def external_processing_consent(self):
        if self.data_classification == "proprietary" and not self.allow_external_processing:
            raise ValueError("Proprietary text requires explicit allow_external_processing consent")
        return self
