from goldenflow.domains import load_domain

def test_load_people_hr():
    pack = load_domain("people_hr")
    assert pack.name == "people_hr"

def test_load_healthcare():
    pack = load_domain("healthcare")
    assert pack.name == "healthcare"
    assert "npi_validate" in pack.transforms

def test_load_finance():
    pack = load_domain("finance")
    assert pack.name == "finance"
    assert "account_mask" in pack.transforms

def test_load_ecommerce():
    pack = load_domain("ecommerce")
    assert pack.name == "ecommerce"
    assert "sku_normalize" in pack.transforms

def test_load_real_estate():
    pack = load_domain("real_estate")
    assert pack.name == "real_estate"
    assert "mls_normalize" in pack.transforms
