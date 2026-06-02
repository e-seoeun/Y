"""
motion.py
---------
관측 streak / 위성 후보의 angular motion (각속도, position angle) 계산.

ObservedMotion:
    두 점(before, after)의 ra/dec/time → great-circle 각속도(deg/s) 와
    position angle(deg, N→E, 0~360) 계산.

compute_candidate_motion(rows):
    candidate_search.search_candidates() 의 raw rows (TfC_s ∈ {-1, 0, +1}) →
    각 위성별 {w_cand, pa_cand, cand_before/mid/after, AngDst_deg, ...} dict 리스트.
"""

from datetime import datetime

import numpy as np
import pandas as pd
from astropy.time import Time


# =============================================================================
# Two-point angular motion
# =============================================================================
class ObservedMotion:
    """
    두 관측점 (before, after) 의 great-circle 각속도와 position angle.

    각 점은 dict 또는 pandas.Series 로, 다음 키 중 일부를 가져야 함:
        ra_ls   또는 ra
        dec_ls  또는 dec
        time    또는 mjd  (datetime / str / float / astropy Time)
    """

    def __init__(self, before, after):
        self.ra1_deg = before.get("ra_ls", before.get("ra"))
        self.dec1_deg = before.get("dec_ls", before.get("dec"))
        self.time1 = self._parse_time(before.get("time") or before.get("mjd"))

        self.ra2_deg = after.get("ra_ls", after.get("ra"))
        self.dec2_deg = after.get("dec_ls", after.get("dec"))
        self.time2 = self._parse_time(after.get("time") or after.get("mjd"))

        self.ra1 = np.deg2rad(self.ra1_deg)
        self.dec1 = np.deg2rad(self.dec1_deg)
        self.ra2 = np.deg2rad(self.ra2_deg)
        self.dec2 = np.deg2rad(self.dec2_deg)

    @staticmethod
    def _parse_time(t):
        if isinstance(t, (pd.Series, list, np.ndarray)):
            t = t[0] if isinstance(t, (list, np.ndarray)) else t.iloc[0]
        if isinstance(t, Time):
            return t
        if isinstance(t, float):
            return Time(t, format="mjd", scale="utc")
        if isinstance(t, datetime):
            return Time(t, format="datetime", scale="utc")
        if isinstance(t, str):
            try:
                return Time(t, format="iso", scale="utc")
            except ValueError:
                try:
                    return Time(float(t), format="mjd", scale="utc")
                except Exception as exc:
                    raise ValueError(f"Cannot parse time string: {t}") from exc
        raise ValueError(f"Unsupported time format: {t} (type: {type(t).__name__})")

    def compute(self) -> dict:
        cos_theta = (np.sin(self.dec1) * np.sin(self.dec2)
                     + np.cos(self.dec1) * np.cos(self.dec2)
                     * np.cos(self.ra1 - self.ra2))
        cos_theta = float(np.clip(cos_theta, -1.0, 1.0))
        theta_rad = np.arccos(cos_theta)

        delta_t = (Time(self.time2, scale="utc")
                   - Time(self.time1, scale="utc")).sec
        omega = np.rad2deg(theta_rad) / delta_t if delta_t else 0.0

        d_ra = self.ra2 - self.ra1
        x = (np.cos(self.dec1) * np.tan(self.dec2)
             - np.sin(self.dec1) * np.cos(d_ra))
        y = np.sin(d_ra)
        pa_deg = float(np.rad2deg(np.arctan2(y, x))) % 360.0

        return {"w_cand": round(float(omega), 4),
                "pa_cand": round(pa_deg, 4)}


# =============================================================================
# Candidate-side motion (grouped by RSO over -1 / 0 / +1초 propagation)
# =============================================================================
def compute_candidate_motion(rows):
    """
    search_candidates() 가 반환한 list[dict] 를 rso 별로 묶어 각각의
    angular motion (w_cand, pa_cand) 을 계산.

    각 rso 결과 dict:
        rso, cospar, w_cand, pa_cand,
        cand_before, cand_mid, cand_after  (pandas.Series)
        AngDst_deg (center 만), center_mjd
    """
    if not rows:
        return []

    df = pd.DataFrame(rows)
    results = []

    for rso, grp in df.groupby("rso"):
        grp = grp.sort_values("TfC_s")
        row_before = grp[grp["TfC_s"] == -1.0]
        row_after = grp[grp["TfC_s"] == 1.0]
        row_center = grp[grp["TfC_s"] == 0.0]
        if row_before.empty or row_after.empty:
            continue

        before = row_before.iloc[0].copy()
        after = row_after.iloc[0].copy()
        mid = row_center.iloc[0].copy() if not row_center.empty else None

        # ObservedMotion 이 기대하는 컬럼명 맞춤
        before["ra_ls"] = before["ra_deg"]
        before["dec_ls"] = before["dec_deg"]
        after["ra_ls"] = after["ra_deg"]
        after["dec_ls"] = after["dec_deg"]

        motion = ObservedMotion(before, after).compute()

        item = {
            "rso": rso,
            "cospar": grp.iloc[0]["cospar"],
            "w_cand": motion["w_cand"],
            "pa_cand": motion["pa_cand"],
            "cand_before": before,
            "cand_mid": mid,
            "cand_after": after,
        }
        if mid is not None:
            item["AngDst_deg"] = mid["AngDst_deg"]
            item["center_mjd"] = mid["mjd"]
        results.append(item)

    return results