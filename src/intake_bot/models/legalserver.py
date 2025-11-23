from datetime import date
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class County(BaseModel):
    """Information about a county."""

    county_name: Optional[str] = Field(default=None, description="Name of the county")
    county_state: Optional[str] = Field(default=None, description="Two-letter state abbreviation")
    county_FIPS: Optional[str] = Field(default=None, description="FIPS code for the county")


class User(BaseModel):
    """Reference to a user in LegalServer."""

    user_id: Optional[int] = Field(default=None, description="Numeric user ID")
    user_uuid: Optional[str] = Field(default=None, description="UUID of the user")


class CaseExclusion(BaseModel):
    """A user excluded from a case."""

    user_id: Optional[int] = Field(default=None, description="Numeric user ID")
    user_uuid: Optional[str] = Field(default=None, description="UUID of the user")


class Lookup(BaseModel):
    """A reference to a lookup value."""

    lookup_value_id: Optional[int] = Field(
        default=None, description="Numeric ID of the lookup value"
    )
    lookup_value_uuid: Optional[str] = Field(default=None, description="UUID of the lookup value")
    lookup_value_name: Optional[str] = Field(default=None, description="Name of the lookup value")


class IncomePayload(BaseModel):
    """Payload for creating an income record in LegalServer.

    Uses income category name (lookup_value_name) to identify the income type.
    """

    type: Lookup = Field(
        ..., description="Income type lookup reference with lookup_value_name (required)"
    )
    amount: float = Field(..., description="Income amount (required)")
    period: str = Field(
        ...,
        description="Payment period (required). Valid values: Annually, Quarterly, Monthly, Semi-Monthly, Biweekly, Weekly, or numeric: 1, 4, 12, 24, 26, 52",
    )
    exclude: Optional[bool] = Field(default=False, description="Whether to exclude this income")
    notes: Optional[str] = Field(default=None, description="Notes about the income")

    @field_validator("period", mode="before")
    @classmethod
    def validate_period(cls, v):
        """Validate period is one of the allowed values."""
        valid_periods = {
            "Annually",
            "Quarterly",
            "Monthly",
            "Semi-Monthly",
            "Biweekly",
            "Weekly",
            "1",
            "4",
            "12",
            "24",
            "26",
            "52",
        }
        if v not in valid_periods:
            raise ValueError(f"period must be one of {valid_periods}, got {v}")
        return v


class AdditionalNamePayload(BaseModel):
    """Payload for creating an additional name record in LegalServer."""

    first: str = Field(..., description="First name (required)")
    last: str = Field(..., description="Last name (required)")
    middle: Optional[str] = Field(default=None, description="Middle name")
    suffix: Optional[str] = Field(default=None, description="Name suffix")
    type: Lookup = Field(..., description="Name type lookup reference (required)")

    model_config = ConfigDict(use_enum_values=True)


class AdversePartyPayload(BaseModel):
    """Payload for creating an adverse party record in LegalServer."""

    first: Optional[str] = Field(default=None, description="First name")
    last: Optional[str] = Field(default=None, description="Last name")
    middle: Optional[str] = Field(default=None, description="Middle name")
    suffix: Optional[str] = Field(default=None, description="Name suffix")
    organization_name: Optional[str] = Field(default=None, description="Organization name")
    date_of_birth: Optional[str] = Field(default=None, description="Date of birth (YYYY-MM-DD)")
    phone_home: Optional[str] = Field(default=None, description="Home phone number")
    phone_business: Optional[str] = Field(default=None, description="Business phone number")
    phone_mobile: Optional[str] = Field(default=None, description="Mobile phone number")
    phone_fax: Optional[str] = Field(default=None, description="Fax phone number")

    model_config = ConfigDict(use_enum_values=True)

    @model_validator(mode="after")
    def validate_name_required(self) -> "AdversePartyPayload":
        """Validate that either (first AND last) OR organization_name is provided."""
        has_individual = self.first and self.last
        has_org = self.organization_name
        if not (has_individual or has_org):
            raise ValueError("Either (first AND last) OR organization_name is required")
        return self


class NotePayload(BaseModel):
    """Payload for creating a note record in LegalServer."""

    subject: str = Field(..., description="Note subject (required)")
    body: str = Field(..., description="Note body (required)")
    note_type: Lookup = Field(..., description="Note type lookup reference (required)")
    is_html: Optional[bool] = Field(default=False, description="Whether body is HTML")
    allow_etransfer: Optional[bool] = Field(default=True, description="Allow eTransfer")

    model_config = ConfigDict(use_enum_values=True)


class RejectionReason(BaseModel):
    """Rejection reason lookup object."""

    lookup_value_id: Optional[int] = Field(
        default=None, description="Numeric ID of the lookup value"
    )
    lookup_value_uuid: Optional[str] = Field(default=None, description="UUID of the lookup value")
    lookup_value_name: Optional[str] = Field(default=None, description="Name of the lookup value")
    lookup_type_name: Optional[str] = Field(
        default=None, description="Name of the lookup type", json_schema_extra={"readOnly": True}
    )
    lookup_type_table_name: Optional[str] = Field(
        default=None, description="Table name for the lookup", json_schema_extra={"readOnly": True}
    )
    lookup_type_custom: Optional[bool] = Field(
        default=None,
        description="Whether this is a custom lookup",
        json_schema_extra={"readOnly": True},
    )
    lookup_type_uuid: Optional[str] = Field(
        default=None, description="UUID of the lookup type", json_schema_extra={"readOnly": True}
    )


class Organization(BaseModel):
    """Reference to an organization."""

    organization_id: Optional[int] = Field(default=None, description="Numeric organization ID")
    organization_uuid: Optional[str] = Field(default=None, description="UUID of the organization")
    organization_name: Optional[str] = Field(
        default=None, description="Name of the organization", json_schema_extra={"readOnly": True}
    )


class DynamicProcess(BaseModel):
    """Reference to a dynamic process."""

    dynamic_process_id: Optional[int] = Field(default=None, description="Numeric process ID")
    dynamic_process_uuid: Optional[str] = Field(
        default=None, description="UUID of the dynamic process"
    )


class UpdatePayload(BaseModel):
    """Fields to update in an existing matter."""

    case_disposition: Optional[str] = Field(default=None, description="Case disposition")
    case_id: Optional[int] = Field(default=None, description="Case ID")
    case_number: Optional[str] = Field(default=None, description="Case number")
    cause_number: Optional[str] = Field(default=None, description="Cause number")
    client_email_address: Optional[str] = Field(default=None, description="Client email address")
    date_of_birth: Optional[date] = Field(default=None, description="Client date of birth")
    external_id: Optional[str] = Field(default=None, description="External ID")
    first: Optional[str] = Field(default=None, description="First name")
    intake_office: Optional[str] = Field(default=None, description="Intake office")
    intale_program: Optional[str] = Field(
        default=None, description="Intake program (note: field name has typo in API)"
    )
    is_lead_case: Optional[str] = Field(default=None, description="Whether this is a lead case")
    last: Optional[str] = Field(default=None, description="Last name")
    legal_problem_code: Optional[str] = Field(default=None, description="Legal problem code")
    organization: Optional[str] = Field(default=None, description="Organization")
    phone_number: Optional[str] = Field(default=None, description="Phone number")
    pro_bono_opportunity_status: Optional[str] = Field(
        default=None, description="Pro bono opportunity status"
    )
    rural: Optional[bool] = Field(default=None, description="Whether the case is rural")
    sending_site_identification_number: Optional[str] = Field(
        default=None, description="Sending site identification number"
    )
    ssn: Optional[str] = Field(default=None, description="Social security number")


class LegalServerCreateMatterPayload(BaseModel):
    """Payload for creating a matter in LegalServer.

    Required fields:
    - Either (first AND last) OR organization_name OR organization_uuid
    - case_disposition is required
    """

    # Basic Identifiers
    cause_number: Optional[str] = Field(default=None, description="Cause number")
    case_title: Optional[str] = Field(default=None, description="Title of the case")
    client_id: Optional[int] = Field(
        default=None,
        description="Backend ID number for the client. If this matches other clients on other cases, these cases will be considered associated.",
    )
    external_id: Optional[str] = Field(
        default=None,
        description="External field where the linking ID for an integration can be stored. Searchable and editable via API. Unique constraint applies.",
    )

    # Client Name Information
    prefix: Optional[str] = Field(default=None, description="Name prefix (e.g., Dr., Mr., Ms.)")
    first: str = Field(
        ...,
        description="Client first name. Required field.",
    )
    last: str = Field(
        ...,
        description="Client last name. Required field.",
    )
    middle: Optional[str] = Field(default=None, description="Client middle name")
    suffix: Optional[str] = Field(default=None, description="Name suffix (e.g., Jr., Sr.)")

    # Client Type
    is_group: Optional[bool] = Field(
        default=False, description="Whether the client is a group/organization entity"
    )
    organization_name: Optional[str] = Field(
        default=None,
        description="Name of the organization if is_group=true. Required if is_group=true and organization_uuid not provided",
    )
    organization_uuid: Optional[str] = Field(
        default=None,
        description="UUID of existing organization. Only available if Organizations as Clients feature is enabled. Connects client to organization record and sets Organization Name automatically",
    )

    # Email
    client_email_address: Optional[str] = Field(
        default=None, description="Client email address. Must be a valid email address"
    )

    # Case Status (case_disposition is required)
    case_disposition: Optional[str] = Field(
        default="Incomplete Intake",
        description="Case disposition or status. REQUIRED. Determines automatic validation and field defaults. Validation rules: Incomplete Intake (intake_date=today, others null) | Open (prescreen=false, intake_date/date_open=today) | Closed (prescreen=false, intake_date/date_open/date_closed=today) | Prescreen (prescreen=true, prescreen_date=today, prescreen_user/office/program auto-populated) | Rejected (intake_date/date_rejected=today) | Pending (intake_date=today)",
    )
    case_status: Optional[str] = Field(
        default=None, description="Current case status (separate from disposition)"
    )
    case_type: Optional[str] = Field(default=None, description="Type of case")
    case_number: Optional[str] = Field(default=None, description="Case number assigned by court")
    close_reason: Optional[str] = Field(default=None, description="Reason case was closed")

    # Prescreen Information
    is_this_a_prescreen: Optional[bool] = Field(
        default=False,
        description="Whether this is a prescreen. Auto-populated based on case_disposition",
    )
    prescreen_date: Optional[date] = Field(
        default=None,
        description="Date of prescreen. Defaults to today if case_disposition=Prescreen",
    )
    prescreen_user: Optional[User] = Field(
        default=None,
        description="User who conducted prescreen. Defaults to API user if case_disposition=Prescreen. Can be set via user_id or user_uuid",
    )
    prescreen_program: Optional[str] = Field(
        default=None,
        description="Program for prescreen. Defaults to API user's program if case_disposition=Prescreen",
    )
    prescreen_office: Optional[str] = Field(
        default=None,
        description="Office for prescreen. Defaults to API user's office if case_disposition=Prescreen",
    )
    prescreen_screening_status: Optional[str] = Field(
        default=None, description="Status of prescreen"
    )

    # Intake Information
    intake_office: Optional[str] = Field(default=None, description="Office handling intake")
    intake_program: Optional[str] = Field(default=None, description="Program handling intake")
    intake_user: Optional[User] = Field(default=None, description="User who handled intake")
    intake_date: Optional[date] = Field(default=None, description="Date of intake")
    intake_type: Optional[str] = Field(default=None, description="Type of intake")

    # Key Dates
    date_opened: Optional[date] = Field(default=None, description="Date case was opened")
    date_closed: Optional[date] = Field(default=None, description="Date case was closed")
    date_rejected: Optional[date] = Field(default=None, description="Date case was rejected")
    rejected: Optional[bool] = Field(default=None, description="Whether the case was rejected")
    rejection_reason: Optional[RejectionReason] = Field(
        default=None, description="Reason for case rejection"
    )

    # Mailing Address
    mailing_street: Optional[str] = Field(default=None, description="Mailing street address")
    mailing_apt_num: Optional[str] = Field(default=None, description="Mailing apartment number")
    mailing_street_2: Optional[str] = Field(default=None, description="Mailing secondary street")
    mailing_city: Optional[str] = Field(default=None, description="Mailing city")
    mailing_state: Optional[str] = Field(default=None, description="Mailing state")
    mailing_zip: Optional[str] = Field(default=None, description="Mailing ZIP code")

    # Home Address
    home_street: Optional[str] = Field(default=None, description="Home street address")
    home_apt_num: Optional[str] = Field(default=None, description="Home apartment number")
    home_street_2: Optional[str] = Field(default=None, description="Home secondary street")
    home_city: Optional[str] = Field(default=None, description="Home city")
    home_state: Optional[str] = Field(default=None, description="Home state")
    home_zip: Optional[str] = Field(default=None, description="Home ZIP code")
    home_address_safe: Optional[bool] = Field(
        default=None, description="Whether it is safe to send mail to home address"
    )

    # Demographic Information
    client_gender: Optional[str] = Field(default=None, description="Client gender")
    date_of_birth: Optional[date] = Field(default=None, description="Client date of birth")
    dob_status: Optional[str] = Field(
        default=None, description="Status of date of birth (verified, estimated, etc.)"
    )

    # Social Security & Identification
    ssn: Optional[str] = Field(default=None, description="Social security number")
    ssn_status: Optional[str] = Field(
        default=None, description="Status of SSN (verified, estimated, etc.)"
    )
    a_number: Optional[str] = Field(default=None, description="A-number (immigration)")
    visa_number: Optional[str] = Field(default=None, description="Visa number")
    drivers_license: Optional[str] = Field(default=None, description="Driver's license number")

    # Veteran & Disability Status
    veteran: Optional[bool] = Field(default=None, description="Whether client is a veteran")
    military_status: Optional[str] = Field(default=None, description="Military status")
    military_service: Optional[str] = Field(default=None, description="Details of military service")
    disabled: Optional[bool] = Field(default=None, description="Whether client is disabled")

    # Phone Numbers
    preferred_phone_number: Optional[str] = Field(
        default=None, description="Preferred phone number"
    )
    home_phone: Optional[str] = Field(
        default=None, description="Home phone number (min 1 character)"
    )
    home_phone_safe: Optional[bool] = Field(
        default=None, description="Whether it is safe to call home phone"
    )
    home_phone_note: Optional[str] = Field(default=None, description="Notes about home phone")
    mobile_phone: Optional[str] = Field(
        default=None, description="Mobile phone number (min 1 character)"
    )
    mobile_phone_safe: Optional[bool] = Field(
        default=None, description="Whether it is safe to call mobile phone"
    )
    mobile_phone_note: Optional[str] = Field(default=None, description="Notes about mobile phone")
    other_phone: Optional[str] = Field(default=None, description="Other phone number")
    other_phone_safe: Optional[bool] = Field(
        default=None, description="Whether it is safe to call other phone"
    )
    other_phone_note: Optional[str] = Field(default=None, description="Notes about other phone")
    work_phone: Optional[str] = Field(default=None, description="Work phone number")
    work_phone_safe: Optional[bool] = Field(
        default=None, description="Whether it is safe to call work phone"
    )
    work_phone_note: Optional[str] = Field(default=None, description="Notes about work phone")
    fax_phone: Optional[str] = Field(default=None, description="Fax phone number")
    fax_phone_safe: Optional[bool] = Field(
        default=None, description="Whether it is safe to call fax phone"
    )
    fax_phone_note: Optional[str] = Field(default=None, description="Notes about fax phone")

    # Language Information
    language: Optional[str] = Field(default=None, description="Primary language")
    second_language: Optional[str] = Field(default=None, description="Secondary language")
    interpreter: Optional[bool] = Field(
        default=None, description="Whether client needs an interpreter"
    )

    # County Information
    county_of_residence: Optional[County] = Field(
        default=None, description="County where client resides"
    )
    county_of_dispute: Optional[County] = Field(
        default=None, description="County where legal issue occurred"
    )

    # Legal Problem Information
    legal_problem_code: Optional[str] = Field(default=None, description="LSC legal problem code")
    legal_problem_category: Optional[str] = Field(
        default=None, description="Category of legal problem"
    )
    special_legal_problem_code: Optional[List[str]] = Field(
        default=None, description="Special legal problem codes"
    )

    # Case Characteristics
    impact: Optional[bool] = Field(default=None, description="Whether case has significant impact")
    special_characteristics: Optional[List[str]] = Field(
        default=None, description="Special characteristics of case"
    )

    # Personal/Family Information
    marital_status: Optional[str] = Field(default=None, description="Client marital status")
    number_of_adults: Optional[int] = Field(
        default=None, description="Number of adults in household"
    )
    number_of_children: Optional[int] = Field(
        default=None, description="Number of children in household"
    )

    # Immigration Information
    citizenship: Optional[str] = Field(default=None, description="Citizenship status")
    citizenship_country: Optional[str] = Field(default=None, description="Country of citizenship")
    country_of_origin: Optional[str] = Field(default=None, description="Country of origin")
    immigration_status: Optional[str] = Field(default=None, description="Immigration status")
    birth_city: Optional[str] = Field(default=None, description="City where client was born")
    birth_country: Optional[str] = Field(default=None, description="Country where client was born")

    # Demographics
    race: Optional[str] = Field(default=None, description="Client race")
    ethnicity: Optional[str] = Field(default=None, description="Client ethnicity")
    highest_education: Optional[str] = Field(default=None, description="Highest level of education")

    # Financial & Eligibility
    percentage_of_poverty: Optional[str] = Field(
        default=None, description="Percentage of federal poverty level"
    )
    asset_eligible: Optional[bool] = Field(
        default=None, description="Whether client meets asset eligibility"
    )
    income_eligible: Optional[bool] = Field(
        default=None, description="Whether client meets income eligibility"
    )
    lsc_eligible: Optional[bool] = Field(default=None, description="Whether client is LSC eligible")
    level_of_expertise: Optional[str] = Field(default=None, description="Level of expertise needed")

    # Living Situation & Vulnerability
    current_living_situation: Optional[str] = Field(
        default=None, description="Description of current living situation"
    )
    victim_of_domestic_violence: Optional[bool] = Field(
        default=None, description="Whether client is victim of domestic violence"
    )
    institutionalized: Optional[bool] = Field(
        default=None, description="Whether client is institutionalized"
    )
    institutionalized_at: Optional[Organization] = Field(
        default=None, description="Organization where client is institutionalized"
    )

    # Referral & Outreach
    how_referred: Optional[str] = Field(
        default=None, description="How client was referred to organization"
    )
    school_status: Optional[str] = Field(default=None, description="Student status")
    employment_status: Optional[str] = Field(default=None, description="Employment status")
    referring_organizations: Optional[Organization] = Field(
        default=None, description="Organization referring this client"
    )

    # SSI/Welfare Information
    ssi_welfare_status: Optional[str] = Field(default=None, description="SSI/Welfare status")
    ssi_months_client_has_received_welfare_payments: Optional[int] = Field(
        default=None, description="Number of months receiving welfare payments"
    )
    ssi_welfare_case_num: Optional[str] = Field(default=None, description="Welfare case number")
    ssi_section8_housing_type: Optional[str] = Field(
        default=None, description="Section 8 housing type"
    )
    ssi_eatra: Optional[str] = Field(default=None, description="EATRA status")
    additional_assistance: Optional[List[str]] = Field(
        default=None, description="Types of additional assistance"
    )

    # Conflict Information
    client_conflict_status: Optional[bool] = Field(
        default=None,
        description="Whether there is a client conflict. True indicates conflict with client, shows in UI",
    )
    conflict_status_note: Optional[str] = Field(
        default=None, description="Notes about conflict status"
    )
    adverse_party_conflict_status: Optional[bool] = Field(
        default=None,
        description="Whether there is an adverse party conflict. True indicates conflict with adverse party, shows in UI",
    )
    conflict_status_note_ap: Optional[str] = Field(
        default=None, description="Notes about adverse party conflict"
    )
    conflict_waived: Optional[bool] = Field(
        default=None, description="Whether conflict with client has been waived"
    )
    ap_conflict_waived: Optional[bool] = Field(
        default=None, description="Whether conflict with adverse party has been waived"
    )
    exclude_from_search_results: Optional[bool] = Field(
        default=False,
        description="Whether to exclude case from appearing in Search Results in UI (does not affect API search results)",
    )
    case_restrictions: Optional[List[str]] = Field(default=None, description="Case restrictions")
    case_exclusions: Optional[List[User]] = Field(
        default=None, description="Users excluded from case. Can be set via user_id or user_uuid"
    )

    # Pro Bono Information
    pro_bono_opportunity_summary: Optional[str] = Field(
        default=None, description="Summary of pro bono opportunity"
    )
    pro_bono_opportunity_note: Optional[str] = Field(
        default=None, description="Notes about pro bono opportunity"
    )
    pro_bono_opportunity_available_date: Optional[date] = Field(
        default=None, description="Date opportunity is available"
    )
    pro_bono_opportunity_placement_date: Optional[date] = Field(
        default=None, description="Date opportunity was placed"
    )
    pro_bono_opportunity_county: Optional[County] = Field(
        default=None, description="County for pro bono opportunity"
    )
    pro_bono_engagement_type: Optional[str] = Field(
        default=None, description="Type of pro bono engagement"
    )
    pro_bono_time_commitment: Optional[str] = Field(
        default=None, description="Time commitment for pro bono work"
    )
    pro_bono_urgent: Optional[bool] = Field(
        default=None, description="Whether pro bono opportunity is urgent"
    )
    pro_bono_interest_cc: Optional[str] = Field(
        default=None,
        description="Comma-separated list of email addresses to be notified if the case is placed with a Pro Bono or if there is interest expressed by a Pro Bono Volunteer",
    )
    pro_bono_skills_developed: Optional[List[str]] = Field(
        default=None, description="Skills developed through pro bono"
    )
    pro_bono_appropriate_volunteer: Optional[List[str]] = Field(
        default=None, description="Appropriate volunteers for opportunity"
    )
    pro_bono_expiration_date: Optional[date] = Field(
        default=None, description="Expiration date for pro bono opportunity"
    )
    pro_bono_opportunity_status: Optional[str] = Field(
        default=None, description="Status of pro bono opportunity"
    )
    pro_bono_opportunity_cc: Optional[str] = Field(
        default=None,
        description="Comma-separated list of email addresses to be notified if the case is placed with a Pro Bono or there is interest expressed by a Pro Bono Volunteer. In UI set by checkboxes for notifying specific case assignments",
    )
    pro_bono_opportunity_guardian_ad_litem_certification_needed: Optional[str] = Field(
        default=None, description="Guardian ad litem certification needed"
    )
    pro_bono_opportunity_summary_of_upcoming_dates: Optional[str] = Field(
        default=None, description="Summary of upcoming dates"
    )
    pro_bono_opportunity_summary_of_work_needed: Optional[str] = Field(
        default=None, description="Summary of work needed"
    )
    pro_bono_opportunity_special_issues: Optional[str] = Field(
        default=None, description="Special issues for pro bono"
    )
    pro_bono_opportunity_court_and_filing_fee_information: Optional[str] = Field(
        default=None, description="Court and filing fee information"
    )
    pro_bono_opportunity_paupers_eligible: Optional[str] = Field(
        default=None, description="Paupers eligible status"
    )

    # SimpleJustice Information
    simplejustice_opportunity_legal_topic: Optional[List[str]] = Field(
        default=None, description="SimpleJustice legal topics"
    )
    simplejustice_opportunity_helped_community: Optional[List[str]] = Field(
        default=None, description="Communities helped by SimpleJustice"
    )
    simplejustice_opportunity_skill_type: Optional[List[str]] = Field(
        default=None, description="SimpleJustice skill types"
    )
    simplejustice_opportunity_community: Optional[List[str]] = Field(
        default=None, description="SimpleJustice communities"
    )

    # Case Flags & Attributes
    is_lead_case: Optional[bool] = Field(default=False, description="Whether this is a lead case")
    lead_case: Optional[str] = Field(default=None, description="Reference to lead case")
    pai_case: Optional[bool] = Field(
        default=None, description="Whether this is a Pesticide Application Injury case"
    )
    prior_client: Optional[bool] = Field(
        default=None, description="Whether client was served before"
    )
    client_approved_transfer: Optional[bool] = Field(
        default=None, description="Whether client approved transfer"
    )
    transfer_reject_reason: Optional[str] = Field(
        default=None, description="Reason for transfer rejection"
    )
    transfer_reject_notes: Optional[str] = Field(
        default=None, description="Notes about transfer rejection"
    )

    # Financial Assistance
    asset_assistance: Optional[bool] = Field(
        default=None, description="Whether case involves asset assistance"
    )
    fee_generating: Optional[bool] = Field(
        default=None, description="Whether case is fee-generating"
    )
    rural: Optional[bool] = Field(default=None, description="Whether case is in rural area")
    income_change_significantly: Optional[bool] = Field(
        default=None, description="Whether income changed significantly"
    )
    income_change_type: Optional[str] = Field(default=None, description="Type of income change")

    # Priorities & Tracking
    priorities: Optional[List[str]] = Field(default=None, description="Case priorities")

    # Administrative Fields
    sending_site_identification_number: Optional[str] = Field(
        default=None,
        description="Sending site identification number. Only available on sites with Electronic Case transfer enabled",
    )
    sharepoint_site_library: Optional[str] = Field(
        default=None, description="SharePoint site library"
    )

    # Complex Objects
    dynamic_process: Optional[DynamicProcess] = Field(
        default=None, description="Reference to dynamic process"
    )
    online_intake_payload: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Object of custom fields and values for the ultimate destination if matter is being transferred to different site",
    )
    custom_fields: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Custom fields object. Supports: Custom Lookups, Lookups, Money, Number, Organization (numeric ID), Percent, Text, Time, User (numeric ID), Date (ISO format), Boolean. Fields identified by database name (e.g., sample_lookup_12)",
    )
    custom_results: Optional[List[str]] = Field(
        default=None,
        description="Controls response format. Excluded=full response, empty array=UUID only, array of field names=those fields returned",
    )

    # Update payload for existing matters
    update: Optional[UpdatePayload] = Field(
        default=None, description="Fields to update in existing matter"
    )

    @field_validator("case_disposition", mode="before")
    @classmethod
    def case_disposition_required(cls, v):
        """case_disposition is a required field."""
        if not v:
            raise ValueError("case_disposition is required")
        return v

    @field_validator("first", "last", "organization_name", "organization_uuid", mode="before")
    @classmethod
    def validate_client_identification(cls, v, info):
        """Validate that either (first AND last) OR organization_name OR organization_uuid is provided."""
        # This will be called after all fields are parsed
        # We'll do the actual validation in a model validator
        return v

    @field_validator(
        "prefix",
        "middle",
        "suffix",
        "case_status",
        "case_type",
        "case_number",
        "close_reason",
        "prescreen_program",
        "prescreen_office",
        "prescreen_screening_status",
        "intake_office",
        "intake_program",
        "intake_type",
        "mailing_street",
        "mailing_apt_num",
        "mailing_street_2",
        "mailing_city",
        "mailing_state",
        "mailing_zip",
        "home_street",
        "home_apt_num",
        "home_street_2",
        "home_city",
        "home_state",
        "home_zip",
        "client_gender",
        "dob_status",
        "ssn_status",
        "a_number",
        "visa_number",
        "drivers_license",
        "military_status",
        "military_service",
        "preferred_phone_number",
        "home_phone_note",
        "mobile_phone_note",
        "other_phone_note",
        "work_phone_note",
        "fax_phone_note",
        "language",
        "second_language",
        "legal_problem_code",
        "legal_problem_category",
        "marital_status",
        "citizenship",
        "citizenship_country",
        "country_of_origin",
        "immigration_status",
        "birth_city",
        "birth_country",
        "race",
        "ethnicity",
        "highest_education",
        "percentage_of_poverty",
        "level_of_expertise",
        "current_living_situation",
        "how_referred",
        "school_status",
        "employment_status",
        "ssi_welfare_status",
        "ssi_welfare_case_num",
        "ssi_section8_housing_type",
        "ssi_eatra",
        "conflict_status_note",
        "conflict_status_note_ap",
        "lead_case",
        "transfer_reject_reason",
        "transfer_reject_notes",
        "income_change_type",
        "sending_site_identification_number",
        "sharepoint_site_library",
        "external_id",
        "cause_number",
        "case_title",
        "client_email_address",
        "organization_uuid",
        mode="before",
    )
    @classmethod
    def falsy_to_none(cls, v):
        """Convert empty strings and other falsy values to None."""
        if not v:
            return None
        return v

    @field_validator("citizenship", mode="before")
    @classmethod
    def convert_citizenship_boolean(cls, v):
        """Convert boolean is_citizen to LegalServer citizenship lookup value.

        True -> "Citizen"
        False -> "Non-Citizen"
        """
        if isinstance(v, bool):
            return "Citizen" if v else "Non-Citizen"
        return v

    def validate_client_identification_complete(self) -> None:
        """Validate that either (first AND last) OR organization_name OR organization_uuid is provided.

        This must be called after model initialization.
        """
        has_individual = self.first and self.last
        has_org_name = self.organization_name
        has_org_uuid = self.organization_uuid

        if not (has_individual or has_org_name or has_org_uuid):
            raise ValueError(
                "Either (first AND last) OR organization_name OR organization_uuid must be provided"
            )

    @model_validator(mode="after")
    def validate_after_init(self) -> "LegalServerCreateMatterPayload":
        """Validate client identification and case_disposition after all fields are parsed.

        If organization_name or organization_uuid is provided, first and last are not required.
        Otherwise, first and last (being required fields) will have been validated.
        """
        # If an organization is specified, individual name fields are optional
        has_org_name = self.organization_name
        has_org_uuid = self.organization_uuid

        # If no organization, first and last will be present (they're required fields)
        # If organization is present, first and last can be None
        if not (has_org_name or has_org_uuid):
            # first and last are required fields, so they must be present here
            if not (self.first and self.last):
                raise ValueError(
                    "Either (first AND last) OR organization_name OR organization_uuid must be provided"
                )

        return self

    model_config = ConfigDict(use_enum_values=True)
