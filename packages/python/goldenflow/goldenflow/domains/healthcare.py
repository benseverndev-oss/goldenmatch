from __future__ import annotations

from goldenflow._polars_lazy import pl
from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
from goldenflow.domains.base import DomainPack
from goldenflow.transforms import register_transform

# NOTE: ``npi_validate`` is owned by goldenflow.transforms.identifiers (native-first +
# a registered pure-Python ``scalar`` for the Polars-free columnar path + byte-parity
# corpus). healthcare used to register its OWN ``npi_validate`` here, which clobbered
# the core one whenever this domain loaded — dropping npi_validate off the columnar
# path (the same class of bug as people_hr's ssn_mask). The domain now REFERENCES the
# core ``npi_validate`` by name (see PACK.transforms). Behavior note: the core rejects
# an NPI with embedded letters (e.g. ``"npi:1234567893"``) where the old domain copy
# stripped all non-digits and accepted it — the core (canonical kernel) is stricter and
# consistent with the rest of the identifier family.


@register_transform(name="icd10_format", input_types=["string"], auto_apply=False, priority=50, mode="series")
def icd10_format(series: pl.Series) -> pl.Series:
    """Standardize ICD-10 codes (uppercase, insert dot after 3rd char)."""
    def _fmt(val):
        if val is None:
            return None
        code = val.strip().upper().replace(".", "")
        if len(code) > 3:
            return code[:3] + "." + code[3:]
        return code
    return series.map_elements(_fmt, return_dtype=pl.Utf8)

PACK = DomainPack(
    name="healthcare",
    description="MRN normalization, ICD-10 formatting, NPI validation, date standardization",
    transforms=["npi_validate", "icd10_format", "date_iso8601", "null_standardize", "strip"],
    default_config=GoldenFlowConfig(
        transforms=[
            TransformSpec(column="npi", ops=["npi_validate"]),
            TransformSpec(column="icd10_code", ops=["icd10_format"]),
            TransformSpec(column="service_date", ops=["date_iso8601"]),
            TransformSpec(column="patient_name", ops=["strip", "title_case"]),
        ]
    ),
)
