export { scanData, type ScanOptions, type ScanResultWithSample } from "./scanner.js";
export { maybeSample } from "./sampler.js";
export { applyCorroborationBoost, applyConfidenceDowngrade } from "./confidence.js";
export { applyFixes, type FixEntry, type FixReport } from "./fixer.js";
export { diffData, formatDiffReport, type DiffReport, type SchemaChange, type FindingChange, type StatChange } from "./differ.js";
export { autoTriage, type TriageResult } from "./triage.js";
export { recordScan, loadHistory, getPreviousScan, type ScanRecord } from "./history.js";
export { shouldNotify, sendWebhook } from "./notifier.js";
export { runSchedule, type ScheduleOptions } from "./scheduler.js";
