"""KG Extraction Validator.

Validates extraction results against defined quality criteria.
All extractions must pass validation before bulk processing.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# Validation thresholds
THRESHOLDS = {
    "json_parse_success_rate": 0.80,   # >= 80%
    "min_entities_per_doc": 3,          # >= 3 entities
    "min_relations_per_doc": 1,         # >= 1 relation
    "max_missing_field_rate": 0.20,     # <= 20% missing fields
    "min_confidence_to_save": 0.60,     # < 0.6 -> do not save
}

# Required fields per extraction type
REQUIRED_ENTITY_FIELDS = {"name", "type"}
REQUIRED_EVENT_FIELDS = {"type", "headline"}
REQUIRED_RELATION_FIELDS = {"source", "target", "type"}

# Failure type codes
FAIL_JSON_ERROR = "JSON_ERROR"
FAIL_ENTITY_SHORT = "ENTITY_SHORT"
FAIL_NO_RELATION = "NO_RELATION"
FAIL_MISSING_FIELD = "MISSING_FIELD"


@dataclass
class ExtractionResult:
    """Single document extraction result."""
    news_id: int
    success: bool = False
    json_parsed: bool = False
    entity_count: int = 0
    event_count: int = 0
    relation_count: int = 0
    missing_field_count: int = 0
    total_field_count: int = 0
    failure_types: list = field(default_factory=list)
    raw_response: str = ""
    parsed_data: dict = field(default_factory=dict)
    duration_ms: int = 0
    error_message: str = ""


@dataclass
class BatchValidationReport:
    """Batch validation summary."""
    total: int = 0
    json_success: int = 0
    entity_pass: int = 0
    relation_pass: int = 0
    field_pass: int = 0
    all_pass: int = 0
    total_entities: int = 0
    total_events: int = 0
    total_relations: int = 0
    total_missing_fields: int = 0
    total_fields: int = 0
    failure_type_counts: dict = field(default_factory=dict)
    results: list = field(default_factory=list)


def validate_extraction(news_id: int, raw_response: str,
                        parsed_data: Optional[dict],
                        duration_ms: int = 0) -> ExtractionResult:
    """Validate a single extraction result against all criteria.

    Args:
        news_id: News article ID
        raw_response: Raw LLM output string
        parsed_data: Parsed dict (or None if JSON parse failed)
        duration_ms: Extraction duration in milliseconds

    Returns:
        ExtractionResult with pass/fail details
    """
    result = ExtractionResult(
        news_id=news_id,
        raw_response=raw_response[:500],
        duration_ms=duration_ms,
    )

    # Check 1: JSON parsing
    if parsed_data is None or not isinstance(parsed_data, dict):
        result.json_parsed = False
        result.failure_types.append(FAIL_JSON_ERROR)
        result.error_message = "JSON parse failed"
        return result

    result.json_parsed = True
    result.parsed_data = parsed_data

    entities = parsed_data.get("entities", [])
    events = parsed_data.get("events", [])
    relations = parsed_data.get("relations", [])

    # Check 2: Entity count
    result.entity_count = len(entities)
    if result.entity_count < THRESHOLDS["min_entities_per_doc"]:
        result.failure_types.append(FAIL_ENTITY_SHORT)

    # Check 3: Relation count
    result.relation_count = len(relations)
    if result.relation_count < THRESHOLDS["min_relations_per_doc"]:
        result.failure_types.append(FAIL_NO_RELATION)

    result.event_count = len(events)

    # Check 4: Missing required fields
    missing = 0
    total = 0
    for e in entities:
        for f in REQUIRED_ENTITY_FIELDS:
            total += 1
            if not e.get(f):
                missing += 1
    for ev in events:
        for f in REQUIRED_EVENT_FIELDS:
            total += 1
            if not ev.get(f):
                missing += 1
    for r in relations:
        for f in REQUIRED_RELATION_FIELDS:
            total += 1
            if not r.get(f):
                missing += 1

    result.missing_field_count = missing
    result.total_field_count = total
    if total > 0 and (missing / total) > THRESHOLDS["max_missing_field_rate"]:
        result.failure_types.append(FAIL_MISSING_FIELD)

    # Overall pass: no failure types
    result.success = len(result.failure_types) == 0
    return result


def validate_batch(results: list[ExtractionResult]) -> BatchValidationReport:
    """Validate a batch of extraction results and generate report.

    Args:
        results: List of ExtractionResult from individual validations

    Returns:
        BatchValidationReport with aggregate metrics
    """
    report = BatchValidationReport(total=len(results), results=results)

    for r in results:
        if r.json_parsed:
            report.json_success += 1
        if r.entity_count >= THRESHOLDS["min_entities_per_doc"]:
            report.entity_pass += 1
        if r.relation_count >= THRESHOLDS["min_relations_per_doc"]:
            report.relation_pass += 1

        missing_rate = (r.missing_field_count / r.total_field_count
                        if r.total_field_count > 0 else 0)
        if missing_rate <= THRESHOLDS["max_missing_field_rate"]:
            report.field_pass += 1

        if r.success:
            report.all_pass += 1

        report.total_entities += r.entity_count
        report.total_events += r.event_count
        report.total_relations += r.relation_count
        report.total_missing_fields += r.missing_field_count
        report.total_fields += r.total_field_count

        for ft in r.failure_types:
            report.failure_type_counts[ft] = report.failure_type_counts.get(ft, 0) + 1

    return report


def format_report(report: BatchValidationReport) -> str:
    """Format batch validation report as readable text."""
    n = report.total
    if n == 0:
        return "No results to report."

    json_rate = report.json_success / n * 100
    avg_ent = report.total_entities / n
    avg_evt = report.total_events / n
    avg_rel = report.total_relations / n
    missing_rate = (report.total_missing_fields / report.total_fields * 100
                    if report.total_fields > 0 else 0)
    pass_rate = report.all_pass / n * 100

    lines = []
    lines.append("=" * 60)
    lines.append("  KG EXTRACTION VALIDATION REPORT")
    lines.append(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"  Total documents:        {n}")
    lines.append(f"  All criteria PASS:      {report.all_pass}/{n} ({pass_rate:.0f}%)")
    lines.append("")
    lines.append("  --- Criteria Check ---")
    lines.append(f"  JSON parse success:     {report.json_success}/{n} ({json_rate:.0f}%)"
                 f"  [threshold: >= {THRESHOLDS['json_parse_success_rate']*100:.0f}%]"
                 f"  {'PASS' if json_rate >= THRESHOLDS['json_parse_success_rate']*100 else 'FAIL'}")
    lines.append(f"  Avg entities/doc:       {avg_ent:.1f}"
                 f"  [threshold: >= {THRESHOLDS['min_entities_per_doc']}]"
                 f"  {'PASS' if avg_ent >= THRESHOLDS['min_entities_per_doc'] else 'FAIL'}")
    lines.append(f"  Avg relations/doc:      {avg_rel:.1f}"
                 f"  [threshold: >= {THRESHOLDS['min_relations_per_doc']}]"
                 f"  {'PASS' if avg_rel >= THRESHOLDS['min_relations_per_doc'] else 'FAIL'}")
    lines.append(f"  Missing field rate:     {missing_rate:.1f}%"
                 f"  [threshold: <= {THRESHOLDS['max_missing_field_rate']*100:.0f}%]"
                 f"  {'PASS' if missing_rate <= THRESHOLDS['max_missing_field_rate']*100 else 'FAIL'}")
    lines.append("")

    # Failure type ranking
    sorted_fails = sorted(report.failure_type_counts.items(), key=lambda x: -x[1])
    lines.append("  --- Failure Types (TOP 3) ---")
    for i, (ft, cnt) in enumerate(sorted_fails[:3]):
        labels = {
            FAIL_JSON_ERROR: "JSON parsing error",
            FAIL_ENTITY_SHORT: f"Entity count < {THRESHOLDS['min_entities_per_doc']}",
            FAIL_NO_RELATION: f"Relation count < {THRESHOLDS['min_relations_per_doc']}",
            FAIL_MISSING_FIELD: f"Missing fields > {THRESHOLDS['max_missing_field_rate']*100:.0f}%",
        }
        lines.append(f"  {i+1}. {labels.get(ft, ft)}: {cnt} docs")
    if not sorted_fails:
        lines.append("  (none)")

    lines.append("")
    lines.append("  --- Per-Document Detail ---")
    for r in report.results:
        status = "PASS" if r.success else "FAIL"
        fails = ", ".join(r.failure_types) if r.failure_types else "-"
        lines.append(f"  #{r.news_id}: {status} | E:{r.entity_count} V:{r.event_count}"
                     f" R:{r.relation_count} | {r.duration_ms}ms | {fails}")

    lines.append("")

    # Overall verdict
    batch_pass = (
        json_rate >= THRESHOLDS["json_parse_success_rate"] * 100
        and avg_ent >= THRESHOLDS["min_entities_per_doc"]
        and avg_rel >= THRESHOLDS["min_relations_per_doc"]
        and missing_rate <= THRESHOLDS["max_missing_field_rate"] * 100
    )
    if batch_pass:
        lines.append("  VERDICT: PASS -- 30건 배치 실행 허용")
    else:
        lines.append("  VERDICT: FAIL -- 프롬프트 수정 후 재실행 필요")

    lines.append("=" * 60)
    return "\n".join(lines)


def save_report_to_log(report_text: str, log_dir: str = "logs"):
    """Save report to timestamped log file."""
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    filename = f"kg_extraction_{datetime.now().strftime('%Y%m%d')}.log"
    filepath = Path(log_dir) / filename

    with open(filepath, "a", encoding="utf-8") as f:
        f.write(f"\n{'#'*60}\n")
        f.write(f"# Log entry: {datetime.now().isoformat()}\n")
        f.write(f"{'#'*60}\n")
        f.write(report_text)
        f.write("\n")

    return str(filepath)
