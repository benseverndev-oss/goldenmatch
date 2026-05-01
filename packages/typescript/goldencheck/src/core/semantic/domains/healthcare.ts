import type { TypeDef } from "../../types.js";

export const HEALTHCARE_TYPES: Readonly<Record<string, TypeDef>> = {
  npi: { nameHints: ["npi", "npi_number", "provider_npi", "referring_npi"], valueSignals: { min_unique_pct: 0.90 }, suppress: ["cardinality", "pattern_consistency"] },
  icd_code: { nameHints: ["diagnosis", "dx", "icd", "primary_dx", "secondary_dx", "procedure_code"], valueSignals: { short_strings: true }, suppress: ["type_inference", "cardinality"] },
  insurance_id: { nameHints: ["insurance_id", "policy_number", "member_id", "subscriber_id"], valueSignals: { min_unique_pct: 0.80 }, suppress: ["cardinality"] },
  patient_name: { nameHints: ["patient_name", "patient_first", "patient_last", "attending_physician"], valueSignals: { mixed_case: true }, suppress: ["pattern_consistency", "cardinality"] },
  medical_record: { nameHints: ["record_number", "mrn", "medical_record", "chart_number"], valueSignals: { min_unique_pct: 0.95 }, suppress: ["cardinality", "pattern_consistency", "drift_detection"] },
  cpt_code: { nameHints: ["cpt", "procedure", "hcpcs"], valueSignals: { short_strings: true }, suppress: ["type_inference", "cardinality"] },
  drg_code: { nameHints: ["drg", "drg_code"], valueSignals: { max_unique: 50 }, suppress: ["uniqueness"] },
  facility_code: { nameHints: ["facility_code", "place_of_service", "service_type"], valueSignals: { max_unique: 30 }, suppress: ["uniqueness", "range_distribution"] },
  claim_status: { nameHints: ["claim_status", "adjudication", "auth_status"], valueSignals: { max_unique: 15 }, suppress: ["uniqueness", "range_distribution"] },
  clinical_notes: { nameHints: ["claim_notes", "clinical_notes", "diagnosis_desc", "provider_notes"], valueSignals: { avg_length_min: 15 }, suppress: ["pattern_consistency", "cardinality", "type_inference", "drift_detection"] },
};
