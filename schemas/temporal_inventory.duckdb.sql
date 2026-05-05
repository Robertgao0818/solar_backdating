-- DuckDB schema for the GEHI-backed temporal inventory pilot.
-- Stage contracts remain CSV/Parquet; this file gives an optional local
-- query/index layer without requiring a PostGIS service.

CREATE TABLE IF NOT EXISTS pipeline_run (
    run_id VARCHAR PRIMARY KEY,
    created_at TIMESTAMP DEFAULT current_timestamp,
    code_commit VARCHAR,
    config_hash VARCHAR,
    provider VARCHAR NOT NULL,
    provider_version VARCHAR,
    notes VARCHAR
);

CREATE TABLE IF NOT EXISTS anchor (
    anchor_id VARCHAR PRIMARY KEY,
    region_key VARCHAR NOT NULL,
    grid_id VARCHAR NOT NULL,
    source_annotation_path VARCHAR,
    source_feature_id INTEGER,
    centroid_lon DOUBLE NOT NULL,
    centroid_lat DOUBLE NOT NULL,
    chip_lon_min DOUBLE NOT NULL,
    chip_lat_min DOUBLE NOT NULL,
    chip_lon_max DOUBLE NOT NULL,
    chip_lat_max DOUBLE NOT NULL,
    anchor_policy VARCHAR NOT NULL,
    source_semantics VARCHAR DEFAULT 'source mask is a location anchor, not exact historical mask GT'
);

CREATE TABLE IF NOT EXISTS vintage_candidate (
    vintage_id VARCHAR PRIMARY KEY,
    anchor_id VARCHAR NOT NULL REFERENCES anchor(anchor_id),
    provider VARCHAR NOT NULL DEFAULT 'GEHI_TM',
    zoom INTEGER NOT NULL,
    capture_date DATE NOT NULL,
    version INTEGER NOT NULL,
    complete_coverage BOOLEAN,
    capture_date_min DATE,
    capture_date_max DATE,
    all_capture_dates VARCHAR,
    n_date_labels INTEGER,
    info_stdout_sha256 VARCHAR,
    gehi_command VARCHAR,
    UNIQUE(anchor_id, version)
);

CREATE TABLE IF NOT EXISTS image_artifact (
    artifact_id VARCHAR PRIMARY KEY,
    vintage_id VARCHAR REFERENCES vintage_candidate(vintage_id),
    anchor_id VARCHAR NOT NULL REFERENCES anchor(anchor_id),
    run_id VARCHAR REFERENCES pipeline_run(run_id),
    provider VARCHAR NOT NULL,
    zoom INTEGER NOT NULL,
    capture_date DATE NOT NULL,
    version INTEGER,
    path VARCHAR NOT NULL,
    sha256 VARCHAR,
    crs VARCHAR,
    bounds_wkt VARCHAR,
    width INTEGER,
    height INTEGER,
    exact_date BOOLEAN,
    status VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS gemini_review (
    review_id VARCHAR PRIMARY KEY,
    artifact_id VARCHAR REFERENCES image_artifact(artifact_id),
    run_id VARCHAR REFERENCES pipeline_run(run_id),
    model VARCHAR NOT NULL,
    prompt_hash VARCHAR,
    response_json JSON,
    pv_present BOOLEAN,
    confidence DOUBLE,
    quality_flag VARCHAR,
    decision_source VARCHAR DEFAULT 'gemini',
    reviewed_at TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS presence_observation (
    observation_id VARCHAR PRIMARY KEY,
    anchor_id VARCHAR NOT NULL REFERENCES anchor(anchor_id),
    artifact_id VARCHAR REFERENCES image_artifact(artifact_id),
    capture_date DATE NOT NULL,
    version INTEGER,
    pv_present BOOLEAN,
    pv_score DOUBLE,
    quality_flag VARCHAR NOT NULL,
    decision_source VARCHAR NOT NULL,
    notes VARCHAR
);

CREATE TABLE IF NOT EXISTS install_interval (
    anchor_id VARCHAR NOT NULL REFERENCES anchor(anchor_id),
    run_id VARCHAR REFERENCES pipeline_run(run_id),
    status VARCHAR NOT NULL,
    latest_absent_date DATE,
    earliest_present_date DATE,
    install_interval_start DATE,
    install_interval_end DATE,
    n_observations INTEGER,
    n_absent INTEGER,
    n_present INTEGER,
    confidence VARCHAR,
    notes VARCHAR,
    PRIMARY KEY(anchor_id, run_id)
);

