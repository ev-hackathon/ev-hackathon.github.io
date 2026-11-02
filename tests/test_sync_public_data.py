import io
import json

from scripts import sync_public_data


def test_sync_public_snapshots_only_accepts_known_region_codes(tmp_path, monkeypatch):
    fetched = []
    def fake_open(url, timeout):
        fetched.append(url)
        payload = {'ok': True, 'regions': {'43110': {'t': '1200'}, '../bad': {}}} if url.endswith('status.json') else {
            'date': '2026-11-02', 'hours': {'12': {'t': '1200', 's': {}}}, 'stations': {'test': {}}}
        return io.BytesIO(json.dumps(payload).encode())
    monkeypatch.setattr(sync_public_data, 'urlopen', fake_open)
    assert sync_public_data.sync(tmp_path) == 1
    saved = json.loads((tmp_path / 'status.json').read_text())
    assert list(saved['regions']) == ['43110']
    assert (tmp_path / 'history/43110.json').exists()
    assert len(fetched) == 2
