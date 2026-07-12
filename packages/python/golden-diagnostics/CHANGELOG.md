# Changelog

## 0.1.0

- Initial release. `report_anomaly` / `report_exception` / `issue_url` /
  `environment_report` — anomaly diagnostics that emit a prefilled GitHub issue
  URL. Sends nothing anywhere; warn-once per process; kill switch
  `GOLDEN_DIAGNOSTICS=0`. Never raises.
