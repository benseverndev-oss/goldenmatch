from goldenpipe.repair import fine_type, resolve_tag


def test_iban_classifies_iban():
    assert fine_type("account", ["GB82WEST12345698765432", "DE89370400440532013000"]) == "iban"

def test_routing_9digit_is_not_iban_and_needs_name_for_aba():
    # bare 9-digit, no name hint -> no fine tag
    assert fine_type("col1", ["021000021", "011401533"]) is None
    # with routing name hint -> aba_routing
    assert fine_type("routing_number", ["021000021", "011401533"]) == "aba_routing"

def test_credit_card_needs_luhn():
    assert fine_type("card", ["4539578763621486", "4485275742308327"]) == "credit_card"   # valid Luhn
    assert fine_type("card", ["4539578763621487", "1234567812345678"]) is None            # fail Luhn

def test_barcode_resolves_ean_not_credit_card():
    assert fine_type("barcode", ["4006381333931", "0012345678905"]) == "ean"

def test_minority_match_does_not_fire():
    # only 1 of 3 is an IBAN -> no majority
    assert fine_type("account", ["GB82WEST12345698765432", "n/a", "unknown"]) is None

def test_resolve_tag_prefers_fine_then_coarse_then_none():
    assert resolve_tag("email_addr", "email", ["a@b.com"]) == "email"          # coarse, no fine
    assert resolve_tag("iban", "string", ["GB82WEST12345698765432"]) == "iban" # fine wins
    assert resolve_tag("misc", "string", ["hello"]) is None                    # neither
