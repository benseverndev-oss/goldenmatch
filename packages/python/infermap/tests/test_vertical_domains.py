"""The 4 shipped vertical domain packs (insurance, telecom, real_estate,
education): each registered in goldencheck-types + detects its own vertical
without stealing person/generic data or the existing 5 packs.

Note: detect returns a SINGLE winner, so asserting `<vertical>_df -> <vertical>`
already proves no sibling pack steals it — no separate no-sibling-steal case
needed."""
from types import SimpleNamespace

import pytest
from goldencheck_types import list_domains
from infermap.detect import detect_domain_detailed

_VERTICALS = {
    "insurance": ["policy_number", "claim_number", "premium", "deductible", "coverage_type",
                  "policyholder", "underwriter", "claim_status", "insured_name", "payout"],
    "telecom": ["subscriber_id", "msisdn", "imei", "imsi", "data_usage", "call_duration",
                "plan_name", "network_type", "billing_cycle", "sim_id"],
    "real_estate": ["listing_id", "mls", "property_type", "bedrooms", "bathrooms", "square_feet",
                    "lot_size", "year_built", "asking_price", "city"],
    "education": ["student_id", "enrollment_id", "course_code", "course_name", "gpa",
                  "grade_level", "credits", "semester", "major", "attendance"],
}
_PERSON = ["first_name", "last_name", "email", "phone", "city", "state", "address"]
# existing verticals must still detect correctly with the 4 new packs present
_EXISTING = {
    "hr": ["Employee_ID", "First_Name", "Last_Name", "Age", "Department_Region", "Status",
           "Join_Date", "Salary", "Email", "Phone", "Performance_Score", "Remote_Work"],
    "finance": ["account_number", "currency", "amount", "iban", "transaction_type"],
    "ecommerce": ["order_id", "sku", "product", "price", "category", "shipping_address", "coupon"],
    "healthcare": ["patient_id", "mrn", "diagnosis", "provider", "medication", "claim_status"],
}


def _detect(columns):
    return detect_domain_detailed(SimpleNamespace(columns=columns))


@pytest.mark.parametrize("vertical", list(_VERTICALS))
def test_vertical_registered(vertical):
    assert vertical in list_domains()


@pytest.mark.parametrize("vertical,columns", list(_VERTICALS.items()))
def test_vertical_detects_itself(vertical, columns):
    r = _detect(columns)
    assert r.domain == vertical, (r.domain, r.score)
    assert r.score >= 0.5, r.score


def test_generic_person_detects_no_new_vertical():
    assert _detect(_PERSON).domain not in _VERTICALS


@pytest.mark.parametrize("expected,columns", list(_EXISTING.items()))
def test_existing_verticals_not_stolen(expected, columns):
    assert _detect(columns).domain == expected, _detect(columns).domain
