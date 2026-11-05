from utils import sessions


def row(sid, cid, ts, te, now="", out="50", ctype="04"):
    return {"statId": sid, "chgerId": cid, "lastTsdt": ts, "lastTedt": te, "nowTsdt": now, "output": out, "chgerType": ctype, "delYn": "N"}


def test_new_sessions_recorded_once(tmp_path):
    rows = [row("A", "01", "20261103100000", "20261103104500"), row("A", "02", "20261103093000", "20261103110000", out="7", ctype="02"),
            row("B", "01", "", ""), row("C", "01", "20261103120000", "20261103110000")]   # 빈 값·종료<시작 제외
    assert sessions.append("청주시", rows, tmp_path) == 2
    assert sessions.append("청주시", rows, tmp_path) == 0                                 # 같은 세션은 다시 안 씀
    rows[0] = row("A", "01", "20261103111500", "20261103114000")                          # 같은 충전기 새 세션
    assert sessions.append("청주시", rows, tmp_path) == 1
    df = sessions.load(tmp_path)
    assert len(df) == 3 and set(df["speed"]) == {"급속", "완속"}
    assert df.loc[df["chgerId"] == "02", "minutes"].iat[0] == 90.0
    assert sessions.field_coverage(rows)["lastTsdt"] == 0.75


def test_future_end_is_dropped(tmp_path):
    assert sessions.append("청주시", [row("A", "01", "20991104100000", "20991104104500")], tmp_path) == 0


def test_export_sessions_concatenates_recent_days(tmp_path):
    from datetime import datetime
    from scripts import publish_static
    from utils import logic
    hist = tmp_path / "history"
    for day, rows in (("2026-09-01", ["ZZ1,01,급속,50.0,2026-09-01 10:00:00,2026-09-01 10:30:00,30.0"]),   # 30일보다 오래됨
                      ("2026-11-03", ["A,01,급속,50.0,2026-11-03 10:00:00,2026-11-03 10:30:00,30.0"]),
                      ("2026-11-04", ["B,02,완속,7.0,2026-11-04 01:00:00,2026-11-04 05:00:00,240.0",
                                      "C,01,급속,100.0,2026-11-04 09:00:00,2026-11-04 09:20:00,20.0"])):
        d = hist / "sessions" / day
        d.mkdir(parents=True)
        (d / "43110.csv").write_text("statId,chgerId,speed,kw,start,end,minutes\n" + "\n".join(rows) + "\n", encoding="utf-8")
    out = tmp_path / "_site"
    idx = publish_static.export_sessions(hist, out, datetime(2026, 11, 5, 9, 0, tzinfo=logic.KST))
    text = (out / "data" / "sessions" / "43110.csv").read_text(encoding="utf-8").splitlines()
    assert text[0] == "statId,chgerId,speed,kw,start,end,minutes"     # 헤더 한 번만
    assert [x.split(",")[0] for x in text[1:]] == ["A", "B", "C"]     # 오래된 날짜 제외, 날짜 순
    assert idx["regions"]["43110"]["rows"] == 3 and len(idx["regions"]["43110"]["files"]) == 2
    assert (out / "data" / "sessions" / "index.json").exists()
