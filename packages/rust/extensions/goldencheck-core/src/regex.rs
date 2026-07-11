//! Pyo3-free regex kernels mirroring Polars' `str.contains` / `str.replace_all`
//! (both back onto the `regex` crate, so these are byte-identical). Nulls (`None`)
//! never match and pass through unchanged on replace.
use regex::Regex;

/// Count of non-null values matching `pattern` (mirrors `s.str.contains(p).sum()`).
pub fn str_contains_count(values: &[Option<String>], pattern: &str) -> Result<usize, regex::Error> {
    let re = Regex::new(pattern)?;
    Ok(values
        .iter()
        .filter(|v| v.as_deref().is_some_and(|s| re.is_match(s)))
        .count())
}

/// Three-valued match mask: `None` for a null element, else `Some(is_match)`.
/// The Python seam excludes `None` unconditionally (Polars' `filter` drops null-mask rows).
pub fn str_filter_mask(
    values: &[Option<String>],
    pattern: &str,
) -> Result<Vec<Option<bool>>, regex::Error> {
    let re = Regex::new(pattern)?;
    Ok(values
        .iter()
        .map(|v| v.as_deref().map(|s| re.is_match(s)))
        .collect())
}

/// Element-wise `regex::replace_all` (mirrors `s.str.replace_all`); nulls pass through.
pub fn str_replace_all(
    values: &[Option<String>],
    pattern: &str,
    replacement: &str,
) -> Result<Vec<Option<String>>, regex::Error> {
    let re = Regex::new(pattern)?;
    Ok(values
        .iter()
        .map(|v| {
            v.as_deref()
                .map(|s| re.replace_all(s, replacement).into_owned())
        })
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn v(xs: &[Option<&str>]) -> Vec<Option<String>> {
        xs.iter().map(|x| x.map(String::from)).collect()
    }

    #[test]
    fn counts_non_null_matches() {
        let data = v(&[Some("aXb"), Some("cd"), None, Some("X")]);
        assert_eq!(str_contains_count(&data, "X").unwrap(), 2);
    }
    #[test]
    fn mask_is_three_valued() {
        let data = v(&[Some("X"), Some("y"), None]);
        assert_eq!(
            str_filter_mask(&data, "X").unwrap(),
            vec![Some(true), Some(false), None]
        );
    }
    #[test]
    fn replace_passes_nulls_through() {
        let data = v(&[Some("a1b2"), None]);
        assert_eq!(
            str_replace_all(&data, r"\d", "D").unwrap(),
            v(&[Some("aDbD"), None])
        );
    }
    #[test]
    fn unicode_letter_class_matches_polars_semantics() {
        let data = v(&[Some("Ab12")]);
        assert_eq!(
            str_replace_all(&data, r"\p{L}", "L").unwrap(),
            v(&[Some("LL12")])
        );
    }
}
