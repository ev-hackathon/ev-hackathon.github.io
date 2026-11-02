import json
from datetime import datetime

import config
from scripts import build_web, publish_static
from utils.logic import KST


def test_simulation_assets_embedded_and_monitor_preserved():
    html = build_web.build()
    for marker in ('/*__SIM_', '/*__GEO__*/', '/*__PRICES__*/'):
        assert marker not in html
    for element in ('id="map"', 'id="regions"', 'id="ptable"', 'id="simulation-view"',
                    'window.ESSMonitor', 'ESSimulation', 'sim-form', '결과 CSV 다운로드'):
        assert element in html
    assert html.startswith('<!doctype html>')
    assert html.endswith('</html>\n')


def test_static_build_without_data_includes_demo(tmp_path):
    out = tmp_path / 'site'
    status = publish_static.build_site(tmp_path / 'empty', out, collected=None, error=None, now=datetime(2026, 11, 2, 12, tzinfo=KST))
    assert not status['ok']
    assert json.loads((out / 'data/status.json').read_text(encoding='utf-8'))['regions'] == {}
    html = (out / 'index.html').read_text(encoding='utf-8')
    assert 'DEMO-A' in html and '예시 데이터' in html
    assert (out / '.nojekyll').exists()
    assert html == build_web.build()


def test_generated_docs_current():
    assert (config.ROOT / 'docs/index.html').read_text(encoding='utf-8') == build_web.build()


def test_external_forecast_is_copied_without_generating_one(tmp_path, monkeypatch):
    data = tmp_path / 'model-output'
    data.mkdir()
    monkeypatch.setattr(config, 'DATA_DIR', data)
    out = tmp_path / 'site'
    build_web.copy_forecast(out)
    assert not (out / 'data/forecast.json').exists()
    payload = {'schema_version': 1, 'model_name': 'unit-test-fixture', 'predictions': []}
    (data / 'forecast.json').write_text(json.dumps(payload), encoding='utf-8')
    build_web.copy_forecast(out)
    assert json.loads((out / 'data/forecast.json').read_text()) == payload
