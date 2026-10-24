"""ETL pipeline script for aggregating nightly telemetry."""
import json


def process_records(stream):
    return [json.loads(line) for line in stream]
