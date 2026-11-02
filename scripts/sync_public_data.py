"""Copy public Pages snapshots for local preview; never calls the keyed EV API."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.request import urlopen

import config

PUBLIC = "https://ev-hackathon.github.io/"


def sync(out: Path = config.ROOT / "docs" / "data") -> int:
    def fetch(path):
        with urlopen(PUBLIC + path, timeout=30) as response:
            return json.load(response)

    status = fetch("data/status.json")
    if not status.get("ok") or not status.get("regions"):
        raise ValueError("공개 사이트에 수집된 실제 데이터가 없습니다.")
    (out / "history").mkdir(parents=True, exist_ok=True)
    loaded = {}
    for region in config.REGIONS.values():
        code = region["code"]
        if code not in status["regions"]:
            continue
        try:
            payload = fetch(f"data/history/{code}.json")
            if not payload.get("hours") or not payload.get("stations"):
                raise ValueError("empty history")
            (out / "history" / f"{code}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            loaded[code] = status["regions"][code]
            print(f"[OK] {code}: {len(payload['stations'])} stations ({payload['date']})")
        except Exception as error:
            print(f"[SKIP] {code}: {error}")
    if not loaded:
        raise ValueError("실제 데이터 다운로드 실패. 기존 로컬 상태를 유지합니다.")
    status.update(regions=loaded, source_url=PUBLIC)
    (out / "status.json").write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
    return len(loaded)


if __name__ == "__main__":
    print(f"[OK] {sync()} regions saved to docs/data (public snapshots, not live API)")
