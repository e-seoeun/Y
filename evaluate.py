"""
evaluate.py
-----------
관측 streak vs 위성 후보의 일치도 평가:

  - compute_d1_d2(obs_*, cand_*) : gnomonic 사영 후 선형 fit 차이로 D1/D2 계산
  - evaluate_stage1 / evaluate_stage2 : pass 기준 (Stage 1 / Stage 2)
  - pa_diff_deg(a, b) : 두 PA 각도 차이 (0~180)
  - safe_round       : None / NaN 안전 round
"""

import numpy as np


# =============================================================================
# 기본 헬퍼
# =============================================================================
def safe_round(value, ndigits: int = 4):
    if value is None:
        return None
    try:
        return round(float(value), ndigits)
    except (TypeError, ValueError):
        return None


def pa_diff_deg(a: float, b: float) -> float:
    """두 position angle (deg) 간 최소 차이 (0~180)."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


# =============================================================================
# D1 / D2 (gnomonic 사영 + 선형 fit)
# =============================================================================
def _wrap180(d):
    return (np.asarray(d) + 180.0) % 360.0 - 180.0


def _unwrap_about(ra_deg, ra0):
    return ra0 + _wrap180(np.asarray(ra_deg) - ra0)


def _xy_gnomonic(ra_deg, dec_deg, ra0, dec0):
    """tangent (gnomonic) projection 평면 좌표 (deg)."""
    ra = np.deg2rad(np.asarray(ra_deg, dtype=float))
    dec = np.deg2rad(np.asarray(dec_deg, dtype=float))
    ra0r = np.deg2rad(float(ra0))
    de0r = np.deg2rad(float(dec0))
    dra = np.arctan2(np.sin(ra - ra0r), np.cos(ra - ra0r))

    denom = (np.sin(de0r) * np.sin(dec)
             + np.cos(de0r) * np.cos(dec) * np.cos(dra))
    eps = 1e-12
    denom = np.where(np.abs(denom) < eps,
                     np.sign(denom) * eps + (denom == 0) * eps,
                     denom)
    x = (np.cos(dec) * np.sin(dra)) / denom
    y = (np.cos(de0r) * np.sin(dec)
         - np.sin(de0r) * np.cos(dec) * np.cos(dra)) / denom
    return np.rad2deg(x), np.rad2deg(y)


def _fit_line_xy(x, y):
    """3점 (또는 2점) 의 선형 fit → (midpoint, u_dir, n_normal)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)

    if len(x) < 2:
        return (np.array([x.mean(), y.mean()]),
                np.array([1.0, 0.0]),
                np.array([0.0, 1.0]))

    m = np.array([(x[0] + x[-1]) * 0.5, (y[0] + y[-1]) * 0.5])
    v = np.array([x[-1] - x[0], y[-1] - y[0]], dtype=float)
    nrm = np.hypot(v[0], v[1])

    if not np.isfinite(nrm) or nrm < 1e-12:
        diffs = np.diff(np.column_stack([x, y]), axis=0)
        lens = np.linalg.norm(diffs, axis=1)
        if len(lens) == 0 or np.max(lens) < 1e-12:
            return m, np.array([1.0, 0.0]), np.array([0.0, 1.0])
        v = diffs[np.argmax(lens)]
        nrm = np.linalg.norm(v)

    u = v / nrm
    n = np.array([-u[1], u[0]])
    return m, u, n


def compute_d1_d2(obs_before, obs_mid, obs_after,
                  cand_before, cand_mid, cand_after):
    """
    obs / cand 3점 → (D1, D2, cos_theta) 반환. 실패 시 (None, None, None).

      D1 : obs 선분 normal 방향(수직거리)으로 cand midpoint 가 떨어진 거리 [deg]
      D2 : obs 선분 방향(평행)으로 떨어진 거리 [deg]
      cos_theta : 두 선분 단위벡터 내적 절대값 (1=평행)
    """
    try:
        ra0 = float(obs_mid["ra_ls"])
        dec0 = float(obs_mid["dec_ls"])

        ra_obs = np.array([float(obs_before["ra_ls"]),
                           float(obs_mid["ra_ls"]),
                           float(obs_after["ra_ls"])])
        dec_obs = np.array([float(obs_before["dec_ls"]),
                            float(obs_mid["dec_ls"]),
                            float(obs_after["dec_ls"])])

        ra_cand = np.array([float(cand_before["ra_deg"]),
                            float(cand_mid["ra_deg"]),
                            float(cand_after["ra_deg"])])
        dec_cand = np.array([float(cand_before["dec_deg"]),
                             float(cand_mid["dec_deg"]),
                             float(cand_after["dec_deg"])])

        ra_obs_u = _unwrap_about(ra_obs, ra0)
        ra_cand_u = _unwrap_about(ra_cand, ra0)

        x_obs, y_obs = _xy_gnomonic(ra_obs_u, dec_obs, ra0, dec0)
        x_cand, y_cand = _xy_gnomonic(ra_cand_u, dec_cand, ra0, dec0)

        m_obs, u_obs, n_obs = _fit_line_xy(x_obs, y_obs)
        m_cand, u_cand, _ = _fit_line_xy(x_cand, y_cand)

        cos_theta = float(abs(np.dot(u_obs, u_cand)))
        d = m_cand - m_obs
        d1 = float(abs(np.dot(d, n_obs)))
        d2 = float(np.dot(d, u_obs))
        return d1, d2, cos_theta

    except Exception:
        return None, None, None


# =============================================================================
# Pass 평가 (Stage 1 / Stage 2)
# =============================================================================
# Stage 1: angdst≤1°, pa_err≤10°, w_rel_err≤20%      (느슨한 1차 합격)
# Stage 2: pa_err≤5°, w_rel_err≤10%, |D1|≤1, |D2|≤5  (Stage1 탈락 위성을 trajectory 일치로 복구)
# =============================================================================
def evaluate_stage1(angdst, pa_err, w_rel_err, d1, d2) -> bool:
    return (
        angdst is not None
        and pa_err is not None
        and w_rel_err is not None
        and d1 is not None
        and d2 is not None
        and angdst <= 1.0
        and pa_err <= 10.0
        and w_rel_err <= 0.20
    )


def evaluate_stage2(pa_err, w_rel_err, d1, d2, passed_stage1: bool) -> bool:
    return (
        not passed_stage1
        and pa_err is not None
        and w_rel_err is not None
        and d1 is not None
        and d2 is not None
        and pa_err <= 5.0
        and w_rel_err <= 0.10
        and abs(d1) <= 1.0
        and abs(d2) <= 5.0
    )