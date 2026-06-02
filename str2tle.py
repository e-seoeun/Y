"""
str2tle.py
-----------
Step 2: 통합 중간 파일 (`_str.txt`) 의 모든 streak 에 대해 TLE/SGP4 기반
위성 식별을 수행하고, 단일 결과 파일 (`_str_m.txt`) 을 생성한다.

  in   : repository/interim/{date}/{site}/{date}_{site}_str.txt
  out  : results/{date}/{site}/{date}_{site}_str_m.txt
  tle  : repository/dot_tle/{date}.tle

결과 컬럼:
  streak_name  N  time  RA_center  Dec_center  MotionAngle  Speed
  NORAD(or no_match)  d1  d2  da  dv

  - d1 / d2 : observation vs candidate 선분의 normal / parallel 거리 (deg)
  - da      : position angle 차이 (deg)
  - dv      : 각속도 상대오차 (|w_cand - w_obs| / |w_obs|)

공개 API:
  run(date_str, site) -> Path     # str_m.txt 경로
"""

from __future__ import annotations

import multiprocessing as mp
import os
import sys
import time as wall_time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional

import numpy as np
import pandas as pd

from candidate_search import build_orbit_cache, search_candidates
from motion import compute_candidate_motion
from evaluate import (
    compute_d1_d2,
    evaluate_stage1, evaluate_stage2,
    pa_diff_deg, safe_round,
)

# 경로 설정 → config.py 한 군데에서 관리
from config import INTERIM_ROOT, TLE_ROOT, RESULTS_ROOT

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, Exception):
    pass


# 식별 파라미터 — 필요 시 조정
SEARCH_RADIUS_DEG = 5.0                                 # 후보 cone 반경 [deg]
N_WORKERS = max(1, (os.cpu_count() or 2) // 2 - 1)      # 병렬 워커 수
PROGRESS_EVERY = 50                                     # 진행률 출력 간격 [streak]

# 출력 컬럼 폭
W_NAME, W_N, W_TIME = 45, 3, 23
W_RA, W_DEC = 11, 11
W_PA, W_SP = 9, 10
W_NORAD, W_DD = 8, 8


# =============================================================================
# _str.txt 파싱
# =============================================================================
def _parse_time_token(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _build_streak(header_toks, body_toks):
    streak_id = header_toks[0]
    t_center = _parse_time_token(header_toks[2])
    ra_center = float(header_toks[3])
    dec_center = float(header_toks[4])
    pa_obs = float(header_toks[5])
    w_obs = float(header_toks[6])

    grid_t, grid_ra, grid_dec, grid_inten = [], [], [], []
    for toks in body_toks:
        if len(toks) < 4:
            continue
        grid_t.append(_parse_time_token(toks[0]))
        grid_ra.append(float(toks[1]))
        grid_dec.append(float(toks[2]))
        try:
            grid_inten.append(float(toks[3]))
        except ValueError:
            grid_inten.append(float("nan"))

    grid_t = pd.to_datetime(grid_t)
    grid_t_sec = (grid_t - grid_t[0]).total_seconds().to_numpy(dtype=float)
    return {
        "streak_id":  streak_id,
        "N":          len(grid_t),
        "t_center":   t_center,
        "ra_center":  ra_center,
        "dec_center": dec_center,
        "pa_obs":     pa_obs,
        "w_obs":      w_obs,
        "grid_t":     grid_t,
        "grid_t_sec": grid_t_sec,
        "grid_ra":    np.asarray(grid_ra, dtype=float),
        "grid_dec":   np.asarray(grid_dec, dtype=float),
        "grid_inten": np.asarray(grid_inten, dtype=float),
    }


def parse_str_file(path: Path) -> List[dict]:
    """`_str.txt` → list[streak dict]."""
    streaks: List[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        cur_header = None
        cur_body: List[List[str]] = []

        def _flush():
            nonlocal cur_header, cur_body
            if cur_header is not None:
                try:
                    streaks.append(_build_streak(cur_header, cur_body))
                except Exception:
                    pass
            cur_header = None
            cur_body = []

        for raw in f:
            ln = raw.strip()
            if not ln:
                continue
            if ln.startswith("---"):
                _flush()
                continue
            toks = ln.split()
            if cur_header is None:
                cur_header = toks
            else:
                cur_body.append(toks)
        _flush()
    return streaks


# =============================================================================
# 1초 grid 보간 (D1/D2 용 row_before/after)
# =============================================================================
def _wrap_ra(ra1, ra0):
    return ra0 + ((ra1 - ra0 + 540.0) % 360.0 - 180.0)


def _interp(streak, t_target: datetime):
    t_sec = (t_target - streak["grid_t"][0]).total_seconds()
    g_t = streak["grid_t_sec"]
    g_ra = streak["grid_ra"]
    g_dec = streak["grid_dec"]
    if t_sec <= g_t[0]:
        return float(g_ra[0]), float(g_dec[0])
    if t_sec >= g_t[-1]:
        return float(g_ra[-1]), float(g_dec[-1])
    i = int(np.searchsorted(g_t, t_sec))
    alpha = (t_sec - g_t[i - 1]) / (g_t[i] - g_t[i - 1])
    ra0 = float(g_ra[i - 1])
    ra1 = _wrap_ra(float(g_ra[i]), ra0)
    dec0 = float(g_dec[i - 1])
    dec1 = float(g_dec[i])
    return (ra0 + alpha * (ra1 - ra0)) % 360.0, dec0 + alpha * (dec1 - dec0)


def _obs_rows(streak):
    t_mid = streak["t_center"]
    t_before = t_mid - pd.Timedelta(seconds=1)
    t_after = t_mid + pd.Timedelta(seconds=1)
    ra_b, dec_b = _interp(streak, t_before)
    ra_a, dec_a = _interp(streak, t_after)
    return (
        {"time": pd.Timestamp(t_mid),    "ra_ls": streak["ra_center"], "dec_ls": streak["dec_center"]},
        {"time": pd.Timestamp(t_before), "ra_ls": ra_b,                "dec_ls": dec_b},
        {"time": pd.Timestamp(t_after),  "ra_ls": ra_a,                "dec_ls": dec_a},
    )


# =============================================================================
# 한 streak 후보 평가 → best (passing) candidate
# =============================================================================
def _best_candidate(streak, candidate_motion_list,
                    row_mid, row_before, row_after):
    """passing 후보 중 angdst 가 가장 작은 1개 (없으면 None)."""
    obs_w = streak["w_obs"]
    obs_pa = streak["pa_obs"]

    best = None
    for cand in candidate_motion_list:
        w_cand = cand.get("w_cand")
        pa_cand = cand.get("pa_cand")
        angdst = cand.get("AngDst_deg")
        cand_before = cand.get("cand_before")
        cand_mid = cand.get("cand_mid")
        cand_after = cand.get("cand_after")
        if (w_cand is None or pa_cand is None
                or obs_w in (None, 0) or angdst is None):
            continue

        w_rel_err = abs(w_cand - obs_w) / abs(obs_w)
        pa_err = pa_diff_deg(pa_cand, obs_pa)
        d1 = d2 = None
        if cand_before is not None and cand_mid is not None and cand_after is not None:
            d1, d2, _ = compute_d1_d2(
                row_before, row_mid, row_after,
                cand_before, cand_mid, cand_after,
            )

        passed_s1 = evaluate_stage1(angdst, pa_err, w_rel_err, d1, d2)
        passed_s2 = evaluate_stage2(pa_err, w_rel_err, d1, d2, passed_s1)
        if not (passed_s1 or passed_s2):
            continue

        if best is None or angdst < best["angdst"]:
            best = {
                "rso":    int(cand["rso"]),
                "angdst": float(angdst),
                "d1":     None if d1 is None else float(d1),
                "d2":     None if d2 is None else float(d2),
                "da":     float(pa_err),
                "dv":     float(w_rel_err),
            }
    return best


# =============================================================================
# Multiprocessing worker
# =============================================================================
_WORKER_CACHE = None
_WORKER_SITE = None


def _worker_init(tle_path: str, site_kwargs: dict):
    global _WORKER_CACHE, _WORKER_SITE
    _WORKER_CACHE = build_orbit_cache([tle_path])
    _WORKER_SITE = SimpleNamespace(**site_kwargs)


def _worker_process(streak):
    streak_id = streak.get("streak_id", "<unknown>")
    try:
        time_iso = pd.Timestamp(streak["t_center"]).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        rows = search_candidates(
            orbit_cache=_WORKER_CACHE,
            site=_WORKER_SITE,
            time_iso=time_iso,
            ra0_deg=streak["ra_center"],
            de0_deg=streak["dec_center"],
            radius_deg=SEARCH_RADIUS_DEG,
        )
        cm_list = compute_candidate_motion(rows)
        row_mid, row_before, row_after = _obs_rows(streak)
        best = _best_candidate(streak, cm_list, row_mid, row_before, row_after)
    except Exception:
        best = None
        time_iso = pd.Timestamp(streak.get("t_center", datetime(1970, 1, 1))).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )[:-3]

    if best is None:
        norad, d1, d2, da, dv = "no_match", None, None, None, None
    else:
        norad = str(best["rso"])
        d1 = best["d1"]
        d2 = best["d2"]
        da = best["da"]
        dv = best["dv"]

    return {
        "streak_name":  streak_id,
        "N":            int(streak.get("N", 0)),
        "time":         time_iso,
        "ra_center":    float(streak["ra_center"]),
        "dec_center":   float(streak["dec_center"]),
        "motion_angle": float(streak["pa_obs"]),
        "speed":        float(streak["w_obs"]),
        "norad":        norad,
        "d1": d1, "d2": d2, "da": da, "dv": dv,
    }


# =============================================================================
# 결과 파일 포맷팅
# =============================================================================
def _num(v, prec=4, w=W_DD):
    return f"{v:>{w}.{prec}f}" if v is not None else f"{'-':>{w}s}"


def _format_header() -> str:
    return (
        f"# {'streak_name':<{W_NAME-2}s}  "
        f"{'N':>{W_N}s}  {'time':<{W_TIME}s}  "
        f"{'RA_center':>{W_RA}s}  {'Dec_center':>{W_DEC}s}  "
        f"{'MotionAng':>{W_PA}s}  {'Speed':>{W_SP}s}  "
        f"{'NORAD':>{W_NORAD}s}  "
        f"{'d1':>{W_DD}s}  {'d2':>{W_DD}s}  "
        f"{'da':>{W_DD}s}  {'dv':>{W_DD}s}"
    )


def _format_row(r: dict) -> str:
    return (
        f"{r['streak_name']:<{W_NAME}s}  "
        f"{r['N']:>{W_N}d}  {r['time']:<{W_TIME}s}  "
        f"{r['ra_center']:>{W_RA}.6f}  {r['dec_center']:>{W_DEC}.6f}  "
        f"{r['motion_angle']:>{W_PA}.4f}  {r['speed']:>{W_SP}.6f}  "
        f"{r['norad']:>{W_NORAD}s}  "
        f"{_num(r['d1'])}  {_num(r['d2'])}  "
        f"{_num(r['da'])}  {_num(r['dv'])}"
    )


# =============================================================================
# 공개 API
# =============================================================================
def run(date_str: str, site) -> Path:
    """
    한 (date, site) 에 대해 identification 실행 → str_m.txt 경로 반환.

    Parameters
    ----------
    date_str : "YYYYMMDD"
    site     : classes.sites.Site 또는 lat_deg/lon_deg/alt_m 속성을 가진 객체
    """
    site_name = site.name if hasattr(site, "name") else str(site)
    str_path = INTERIM_ROOT / date_str / site_name / f"{date_str}_{site_name}_str.txt"
    tle_path = TLE_ROOT / f"{date_str}.tle"

    if not str_path.exists():
        raise FileNotFoundError(
            f"_str.txt not found: {str_path}\n"
            f"  → ftp2str.run({date_str!r}, site) 를 먼저 실행하세요."
        )
    if not tle_path.exists():
        raise FileNotFoundError(f"TLE not found: {tle_path}")

    out_dir = RESULTS_ROOT / date_str / site_name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{date_str}_{site_name}_str_m.txt"

    print()
    print("=== Step 2: identify ===")
    print(f"  date     : {date_str}")
    print(f"  site     : {site_name} "
          f"(lat={site.lat_deg}, lon={site.lon_deg}, alt={site.alt_m} m)")
    print(f"  input    : {str_path}")
    print(f"  TLE      : {tle_path}")
    print(f"  output   : {out_path}")

    streaks = parse_str_file(str_path)
    streaks = [s for s in streaks if len(s["grid_t"]) >= 2]
    n_total = len(streaks)
    print(f"  streaks  : {n_total}")
    if n_total == 0:
        return out_path

    print("  pre-check orbit cache ...")
    _main_cache = build_orbit_cache([str(tle_path)])
    print(f"  orbit cache size : {len(_main_cache)}")
    del _main_cache

    site_kwargs = dict(
        lat_deg=site.lat_deg,
        lon_deg=site.lon_deg,
        alt_km=site.alt_m / 1000.0,
    )

    print(f"  workers  : {N_WORKERS}")
    ctx = mp.get_context("spawn")

    results: List[dict] = []
    n_done = n_matched = 0
    t_start = wall_time.time()

    with ctx.Pool(
        processes=N_WORKERS,
        initializer=_worker_init,
        initargs=(str(tle_path), site_kwargs),
    ) as pool:
        for r in pool.imap_unordered(_worker_process, streaks, chunksize=4):
            results.append(r)
            n_done += 1
            if r["norad"] != "no_match":
                n_matched += 1
            if n_done % PROGRESS_EVERY == 0 or n_done == n_total:
                elapsed = wall_time.time() - t_start
                eta = elapsed / n_done * (n_total - n_done) if n_done else 0
                print(f"    [{n_done}/{n_total}] matched={n_matched}  "
                      f"elapsed={elapsed:.1f}s  ETA={eta:.1f}s")

    results.sort(key=lambda x: x["time"])
    with open(out_path, "w", encoding="utf-8") as fout:
        fout.write(_format_header() + "\n")
        for r in results:
            fout.write(_format_row(r) + "\n")

    print(f"  DONE     : matched={n_matched}/{n_total}, "
          f"no_match={n_total - n_matched}, "
          f"total={wall_time.time() - t_start:.1f}s")
    return out_path