"""
웹사이트 빌드: web/index.template.html + data/chungbuk_sigungu.geojson → docs/index.html

docs/index.html은 GitHub Pages(설정 > Pages > main 브랜치 /docs)로 바로 공개할 수 있는 단일 파일입니다.
사용법 (프로젝트 루트에서):  python -m scripts.build_web
"""
from __future__ import annotations

import json
import shutil

import config
from utils import prices

TEMPLATE = config.ROOT / "web" / "index.template.html"
OUT = config.ROOT / "docs" / "index.html"


def copy_forecast(out_dir):
    """Optional externally produced model output; no synthetic forecast is generated."""
    source = config.DATA_DIR / "forecast.json"
    if source.exists():
        json.loads(source.read_text(encoding="utf-8"))
        target = out_dir / "data" / "forecast.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def _round(coords):
    if coords and isinstance(coords[0], list):
        return [_round(c) for c in coords]
    return [round(coords[0], 4), round(coords[1], 4)]


def build() -> str:
    geo = json.loads(config.BOUNDARY_FILE.read_text(encoding="utf-8"))
    for f in geo["features"]:
        f["geometry"]["coordinates"] = _round(f["geometry"]["coordinates"])
    geo_txt = json.dumps({"type": "FeatureCollection", "features": geo["features"]}, ensure_ascii=False, separators=(",", ":"))
    price_txt = json.dumps(prices.web_payload(), ensure_ascii=False, separators=(",", ":"))
    sim_config = json.dumps({"threshold": config.DEFAULT_POLICY.discount_threshold,
                             "discount": config.DEFAULT_POLICY.discount_rate,
                             "surcharge": config.DEFAULT_POLICY.surcharge_rate,
                             "regions": [{"name": name, "code": r["code"]} for name, r in config.REGIONS.items()]}, ensure_ascii=False)
    body = (TEMPLATE.read_text(encoding="utf-8").replace("/*__GEO__*/", geo_txt)
            .replace("/*__PRICES__*/", price_txt)
            .replace("/*__SIM_CONFIG__*/", sim_config)
            .replace("/*__SIM_CSS__*/", (TEMPLATE.parent / "simulation.css").read_text(encoding="utf-8"))
            .replace("/*__SIM_ENGINE__*/", (TEMPLATE.parent / "simulation.js").read_text(encoding="utf-8"))
            .replace("/*__SIM_UI__*/", (TEMPLATE.parent / "simulation-ui.js").read_text(encoding="utf-8")))
    head, rest = body.split("\n<header", 1)
    return ('<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            + head.replace("<style>", "<style>\n[hidden]{display:none!important}", 1)
            + "\n</head>\n<body>\n<header" + rest + "\n</body>\n</html>\n")


if __name__ == "__main__":
    OUT.write_text(build(), encoding="utf-8")
    copy_forecast(OUT.parent)
    print(f"✔ {OUT.relative_to(config.ROOT)} ({OUT.stat().st_size // 1024} KB)")
