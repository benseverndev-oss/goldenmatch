//! Pyo3-free date kernel mirroring Polars' `str.to_date(strict=False)` (both back
//! onto `chrono`, so parse-validity + canonical output are byte-identical). Nulls
//! and unparseable strings map to `None`; parsed dates re-emit as canonical ISO
//! `%Y-%m-%d` (the seam converts to `datetime.date`).
use chrono::NaiveDate;

pub fn str_to_date(values: &[Option<String>], fmt: &str) -> Vec<Option<String>> {
    values
        .iter()
        .map(|v| match v.as_deref() {
            None => None,
            Some(s) => NaiveDate::parse_from_str(s, fmt)
                .ok()
                .map(|d| d.format("%Y-%m-%d").to_string()),
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    fn v(xs: &[Option<&str>]) -> Vec<Option<String>> {
        xs.iter().map(|x| x.map(String::from)).collect()
    }

    #[test]
    fn parses_valid_and_nulls_failures() {
        let data = v(&[
            Some("2021-01-05"),
            Some("2021-1-5"),
            Some("2021-13-01"),
            Some("2021-02-30"),
            Some(""),
            Some("nope"),
            None,
            Some("2021-01-05x"),
        ]);
        let got = str_to_date(&data, "%Y-%m-%d");
        assert_eq!(
            got,
            v(&[
                Some("2021-01-05"),
                Some("2021-01-05"),
                None,
                None,
                None,
                None,
                None,
                None
            ])
        );
    }
}
