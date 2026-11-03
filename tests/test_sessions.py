from utils import sessions


def row(sid, cid, ts, te, now="", out="50", ctype="04"):
    return {"statId": sid, "chgerId": cid, "lastTsdt": ts, "lastTedt": te, "nowTsdt": now, "output": out, "chgerType": ctype, "delYn": "N"}


def test_new_sessions_recorded_once(tmp_path):
    rows = [row("A", "01", "20261104100000", "20261104104500"), row("A", "02", "20261104093000", "20261104110000", out="7", ctype="02"),
            row("B", "01", "", ""), row("C", "01", "20261104120000", "20261104110000")]   # 빈 값·종료<시작 제외
    assert sessions.append("청주시", rows, tmp_path) == 2
    assert sessions.append("청주시", rows, tmp_path) == 0                                 # 같은 세션은 다시 안 씀
    rows[0] = row("A", "01", "20261104111500", "20261104114000")                          # 같은 충전기 새 세션
    assert sessions.append("청주시", rows, tmp_path) == 1
    df = sessions.load(tmp_path)
    assert len(df) == 3 and set(df["speed"]) == {"급속", "완속"}
    assert df.loc[df["chgerId"] == "02", "minutes"].iat[0] == 90.0
    assert sessions.field_coverage(rows)["lastTsdt"] == 0.75
