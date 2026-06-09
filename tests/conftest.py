from pathlib import Path
import pandas as pd
import pytest
from pipelines.pipeline import run_pipeline
from pipelines.schema import materialize_frames, SCHEMA

# Global aggregators to collect telemetry metrics during the test lifecycle
telemetry = {
    "tables_checked": 0,
    "total_records_validated": 0,
    "critical_failures": 0,
}

def pytest_addoption(parser):
    parser.addoption(
        "--season-id",
        action="store",
        default="777",
        help="Pulse competition season id to validate.",
    )
    parser.addoption(
        "--test-from-snapshot",
        action="store",
        default="",
        help="Specify a local run folder path to bypass live pipeline network execution.",
    )

@pytest.fixture(scope="session")
def season_id(pytestconfig) -> int:
    return int(pytestconfig.getoption("--season-id"))

@pytest.fixture(scope="session")
def raw_frames(pytestconfig, season_id):
    snapshot_path_str = pytestconfig.getoption("--test-from-snapshot").strip()
    
    if snapshot_path_str:
        if snapshot_path_str.lower() == "latest":
            base_dir = Path("data/snapshots")
            manifests = list(base_dir.glob("**/run=*/manifest.json"))
            
            if not manifests:
                raise FileNotFoundError(
                    "No snapshots found in 'data/snapshots/'. Run your pipeline with --export-snapshots first."
                )
            
            latest_manifest = max(manifests, key=lambda p: p.parent.name)
            run_dir = latest_manifest.parent
            print(f"\n[AUTO-TEST] Resolved 'latest' to snapshot run: {run_dir}")
        else:
            run_dir = Path(snapshot_path_str)
            
        if not run_dir.exists():
            raise FileNotFoundError(f"Test snapshot directory not found: {run_dir}")
            
        print(f"[OFFLINE TEST MODE] Loading data frames directly from local snapshot: {run_dir}")
        frames = {}
        for table_name in SCHEMA.keys():
            csv_file = run_dir / f"{table_name}.csv"
            if csv_file.exists():
                frames[table_name] = pd.read_csv(csv_file)
            else:
                frames[table_name] = pd.DataFrame()
        return frames

    print("\n[ONLINE ENGINE MODE] Running full live pipeline data harvesting...")
    return run_pipeline(season_id)

@pytest.fixture(scope="session")
def frames(raw_frames):
    if any(not df.empty and "competition_name" in df.columns for df in raw_frames.values() if "dim_seasons" in raw_frames):
        return raw_frames
    return materialize_frames(raw_frames)

@pytest.hookimpl(tryfirst=True)
def pytest_runtest_makereport(item, call):
    """Intercept runtime metadata allocations to calculate executive metrics."""
    if call.when == "call":
        # Increment validated records if explicit metrics are exposed on the node
        if hasattr(item, "data_quality_records"):
            telemetry["total_records_validated"] += item.data_quality_records
            
        if call.excinfo is not None:
            telemetry["critical_failures"] += 1

def pytest_generate_tests(metafunc):
    """Dynamically track monitored target structures based on parameterizations."""
    if "table_name" in metafunc.fixturenames:
        call_spec = getattr(metafunc, "_calls", [])
        telemetry["tables_checked"] = max(telemetry["tables_checked"], len(call_spec))

@pytest.hookimpl(optionalhook=True)
def pytest_html_report_title(report):
    report.title = "Premier League Data Quality Dashboard"

def pytest_html_results_summary(prefix, summary, postfix):
    """Blocks the default Environment meta-table and replaces it with custom KPI panels."""
    prefix.clear()  # Removes the default environment structure completely
    
    total_structures = max(1, telemetry["tables_checked"])
    total_passed_structures = max(0, total_structures - telemetry["critical_failures"])
    pass_rate = (total_passed_structures / total_structures) * 100

    html_dashboard = f"""
    <div class="kpi-dashboard-container">
        <div class="kpi-card text-primary">
            <div class="kpi-val">{telemetry['tables_checked']}</div>
            <div class="kpi-lbl">Structures Monitored</div>
        </div>
        <div class="kpi-card text-success">
            <div class="kpi-val">{telemetry['total_records_validated']:,}</div>
            <div class="kpi-lbl">Records Evaluated</div>
        </div>
        <div class="kpi-card {'text-success' if pass_rate == 100 else 'text-warning'}">
            <div class="kpi-val">{pass_rate:.1f}%</div>
            <div class="kpi-lbl">Pipeline Pass Rate</div>
        </div>
        <div class="kpi-card {'text-danger' if telemetry['critical_failures'] > 0 else 'text-muted'}">
            <div class="kpi-val">{telemetry['critical_failures']}</div>
            <div class="kpi-lbl">Integrity Alerts</div>
        </div>
    </div>
    <h2 class="section-title-override">Detailed Quality Audit Logs</h2>
    """
    prefix.append(html_dashboard)