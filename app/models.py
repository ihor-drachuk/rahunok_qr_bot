from typing import Literal, get_args

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
    has_invoice_reference: bool = Field(False, description="Whether the source states the invoice's own number or date")
    notes: str | None = Field(None, description="Ambiguities or warnings about the extraction")


FieldName = Literal["recipient_name", "iban", "edrpou_rnokpp", "amount", "payment_purpose"]
FIELD_NAMES: tuple[FieldName, ...] = get_args(FieldName)


class PurposeFields(BaseModel):
    invoice_number: str | None = Field(None, description='The invoice\'s own number exactly as written, without the "№" sign')
    invoice_date: str | None = Field(None, description="The invoice's date exactly as written")
    goods_kind: Literal["goods", "services"] = Field(description="Whether the invoice is for goods or for services")
    item_name: str | None = Field(
        None, description="The name of the only item exactly as written, when the invoice lists exactly one item")


PurposeFieldName = Literal["invoice_number", "invoice_date", "goods_kind", "item_name"]
PURPOSE_FIELD_NAMES: tuple[PurposeFieldName, ...] = get_args(PurposeFieldName)


class FieldVerdict(BaseModel):
    field: FieldName
    matches: bool = Field(description="Whether the extracted value faithfully matches the source")
    corrected_value: str | None = Field(None, description="The correct value when matches is false")


class ValidationVerdict(BaseModel):
    fields: list[FieldVerdict]
    all_match: bool = Field(description="True only if every field matches the source")


class PurposeFieldVerdict(FieldVerdict):
    field: PurposeFieldName


class PurposeValidationVerdict(BaseModel):
    requisites: list[FieldVerdict]
    purpose: list[PurposeFieldVerdict]
