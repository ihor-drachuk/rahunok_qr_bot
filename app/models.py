from typing import Literal

from pydantic import BaseModel, Field


class GateVerdict(BaseModel):
    contains_requisites: bool = Field(description="Whether the content plausibly contains bank payment requisites")


class ExtractedRequisites(BaseModel):
    recipient_name: str | None = Field(
        None, description="Name of the payment RECIPIENT (who receives the money, not the payer); "
                          "legal form abbreviated, proper name kept in its original letter case")
    iban: str | None = Field(
        None, description='The RECIPIENT\'s IBAN: "UA" followed by 27 digits, uppercase, no spaces')
    edrpou_rnokpp: str | None = Field(
        None, description="The RECIPIENT's EDRPOU (8 digits) or RNOKPP (10 digits)")
    amount: str | None = Field(None, description='Payment amount as a plain number with "." decimal separator')
    payment_purpose: str | None = Field(None, description="The COMPLETE payment purpose text from the source")
    notes: str | None = Field(None, description="Ambiguities or warnings about the extraction")


FieldName = Literal["recipient_name", "iban", "edrpou_rnokpp", "amount", "payment_purpose"]


class FieldVerdict(BaseModel):
    field: FieldName
    matches: bool = Field(description="Whether the extracted value faithfully matches the source")
    corrected_value: str | None = Field(None, description="The correct value when matches is false")


class ValidationVerdict(BaseModel):
    fields: list[FieldVerdict]
    all_match: bool = Field(description="True only if every field matches the source")
