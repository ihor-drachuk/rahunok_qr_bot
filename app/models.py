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
    has_invoice_reference: bool = Field(False, description="Whether the source states the invoice's own number or date")
    notes: str | None = Field(None, description="Ambiguities or warnings about the extraction")


class PurposeFields(BaseModel):
    invoice_number: str | None = Field(None, description='The invoice\'s own number exactly as written, without the "№" sign')
    invoice_date: str | None = Field(None, description="The invoice's date exactly as written")
    goods_kind: Literal["goods", "services"] = Field(description="Whether the invoice is for goods or for services")
    item_name: str | None = Field(
        None, description="The name of the only item exactly as written, when the invoice lists exactly one item")


class FieldCheck(BaseModel):
    matches: bool = Field(description="Whether the extracted value faithfully matches the source")


class Checks(BaseModel):
    """A verdict whose every field is a `FieldCheck`.
    A subclass must not give a field a default, because the structured-output schema would then not require that field,
    so a verdict could leave it unchecked."""

    @property
    def all_match(self) -> bool:
        return all(getattr(self, name).matches for name in type(self).model_fields)


class RequisitesVerdict(Checks):
    recipient_name: FieldCheck
    iban: FieldCheck
    edrpou_rnokpp: FieldCheck
    amount: FieldCheck
    payment_purpose: FieldCheck


class PurposeVerdict(Checks):
    invoice_number: FieldCheck
    invoice_date: FieldCheck
    goods_kind: FieldCheck
    item_name: FieldCheck


class PurposeValidationVerdict(BaseModel):
    requisites: RequisitesVerdict
    purpose: PurposeVerdict
