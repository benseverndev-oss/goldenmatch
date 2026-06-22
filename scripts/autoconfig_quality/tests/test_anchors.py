from scripts.autoconfig_quality.anchors import crm_df, gen_labeled, make_healthcare_df


def test_crm_df_shape():
    df = crm_df()
    assert df.height == 30
    assert "email" in df.columns and "phone" in df.columns


def test_gen_labeled_returns_df_and_rowindex_gt():
    df, gt = gen_labeled(n_entities=50, seed=7)
    assert df.height >= 50
    # GT pairs are row-index tuples i<j
    assert all(isinstance(a, int) and isinstance(b, int) and a < b for a, b in gt)
    assert max(b for _, b in gt) < df.height  # indices in range


def test_make_healthcare_df_has_zip5():
    df = make_healthcare_df(2000, seed=715, zip_present=0.5)
    assert "zip5" in df.columns and "matching_id" in df.columns
