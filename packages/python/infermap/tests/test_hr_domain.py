"""The shipped `hr` domain pack: registered in goldencheck-types (the registry
detect reads) + scores employee data as `hr` without false-positiving on
generic person or ecommerce data."""
from types import SimpleNamespace

from goldencheck_types import list_domains
from infermap.detect import detect_domain_detailed

_EMPLOYEE = [
    "Employee_ID", "First_Name", "Last_Name", "Age", "Department_Region",
    "Status", "Join_Date", "Salary", "Email", "Phone", "Performance_Score", "Remote_Work",
]
_PERSON = ["first_name", "last_name", "email", "phone", "city", "state"]
_ECOM = [
    "product", "sku", "price", "category", "department", "product_title",
    "brand", "rating", "order_status", "shipping_address",
]


def _detect(columns):
    return detect_domain_detailed(SimpleNamespace(columns=columns))


def test_hr_registered_in_goldencheck_types():
    # The registry `detect` actually reads (NOT infermap.dictionaries.available_domains).
    assert "hr" in list_domains()


def test_employee_data_detects_hr_confidently():
    r = _detect(_EMPLOYEE)
    assert r.domain == "hr", (r.domain, r.score)
    assert r.score >= 0.5, r.score


def test_generic_person_data_does_not_detect_hr():
    assert _detect(_PERSON).domain != "hr"


def test_wide_ecommerce_detects_ecommerce_not_hr():
    r = _detect(_ECOM)
    assert r.domain != "hr", (r.domain, r.score)
