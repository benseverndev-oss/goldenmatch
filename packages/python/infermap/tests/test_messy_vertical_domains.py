"""The 7 shipped "messier" vertical packs (logistics, energy, automotive, legal,
hospitality, manufacturing, marketing): each detects its own vertical without
stealing person/generic data or the existing 9 packs.

Note: detect returns a SINGLE winner, so `<vertical>_df -> <vertical>` already
proves no sibling pack steals it. The two documented inherent overlaps
(logistics<->ecommerce order fulfillment; marketing sub-0.5 on light-CRM) are
asserted explicitly below."""
from types import SimpleNamespace

import pytest
from goldencheck_types import list_domains
from infermap.detect import detect_domain_detailed

_VERTICALS = {
    "logistics": ["tracking_number", "shipment_id", "carrier", "waybill", "container_number",
                  "consignee", "freight", "warehouse_id", "delivery_status", "dispatch_date"],
    "energy": ["meter_id", "meter_reading", "kwh_consumed", "tariff", "utility_account",
               "billing_period", "peak_demand", "service_point", "supply_address", "rate_class"],
    "automotive": ["vin", "license_plate", "registration_number", "odometer", "mileage",
                   "make", "model", "trim", "fuel_type", "color"],
    "legal": ["case_number", "docket_number", "matter_id", "plaintiff", "defendant", "attorney",
              "jurisdiction", "court", "filing_date", "status"],
    "hospitality": ["reservation_id", "booking_reference", "check_in", "check_out", "room_type",
                    "room_number", "guest_name", "nights", "occupancy", "rate_per_night"],
    "manufacturing": ["part_number", "work_order", "batch_number", "lot_number", "serial_number",
                      "bom", "assembly_id", "defect_rate", "production_date", "machine_id"],
    "marketing": ["lead_id", "lead_source", "campaign_name", "opportunity_id", "mql",
                  "conversion_rate", "funnel_stage", "utm_source", "email", "contact_name"],
}
_PERSON = ["first_name", "last_name", "email", "phone", "city", "state", "address"]
_SALES_CONTACT = ["contact_name", "email", "phone", "company", "title", "notes"]
_EXISTING = {
    "finance": ["account_number", "currency", "amount", "iban", "transaction_type"],
    "ecommerce": ["order_id", "sku", "product", "price", "category", "shipping_address", "coupon"],
    "hr": ["Employee_ID", "First_Name", "Last_Name", "Department_Region", "Status", "Join_Date",
           "Salary", "Email", "Performance_Score"],
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


@pytest.mark.parametrize("columns", [_PERSON, _SALES_CONTACT])
def test_person_data_detects_no_new_vertical(columns):
    assert _detect(columns).domain not in _VERTICALS


@pytest.mark.parametrize("expected,columns", list(_EXISTING.items()))
def test_existing_verticals_not_stolen(expected, columns):
    assert _detect(columns).domain == expected, _detect(columns).domain


def test_order_flavored_data_resolves_to_ecommerce_not_logistics():
    # Documented overlap: order-centric data (order_id + shipping_address) is
    # ecommerce, not logistics — neither steals the other's pure data.
    cols = ["order_id", "tracking_number", "carrier", "shipping_address", "delivery_status", "warehouse_id"]
    assert _detect(cols).domain == "ecommerce", _detect(cols).domain
