"""
ftp2str.py
-----------
Step 1: 여러 카메라의 raw FTP 관측을 날짜/사이트 단위로 통합한
중간 파일 (`_str.txt`) 을 만든다.

  raw  :  repository/observed_data/{date}/{site}/{date}_{site}_cam{N}.txt
  out  :  repository/interim/{date}/{site}/{date}_{site}_str.txt

각 streak 출력 포맷:
  line 1   : streak_id  N  time  RA_center  Dec_center  MotionAngle  Speed
  line 2~N : time  RA  Dec  MeanIntensity         (1초 간격 샘플)
  ------------------------                        (streak 구분자)

핵심 정의:
  - center : streak 시작~끝의 중간 시각
  - 1초 grid : center 기준 ±1, ±2, ... 초로 양쪽 대칭. K=floor(min(t_center, dur-t_center))
                → N = 2K+1 (홀수). center 가 grid 정중앙 row.
  - RA/Dec (sample) : raw 관측점에 LS 1차 fit 후 grid 시각에서 평가
  - MeanIntensity   : raw 관측점 중 [t-0.5s, t+0.5s) 안에 있는 inten 의 평균

공개 API:
  run(date_str, site, cams=None) -> Path     # Path of generated _str.txt
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

# 경로 설정 → config.py 한 군데에서 관리
from config import OBS_ROOT, INTERIM_ROOT as OUT_ROOT

# 사전 필터 (이 기준 미만은 _str.txt 에 안 들어감) — 필요 시 조정
MIN_DURATION_S = 2.0
MIN_N_OBS = 25

STREAK_SEP = "------------------------"


# =============================================================================
# 파일/카메라 헬퍼
# =============================================================================
def _raw_filename(site_name: str, date_str: str, cam: str) -> str:
    if site_name == "YoungYang_SSA":
        return f"{date_str}_{site_name}_data{cam}.txt"
    if site_name == "SSA_JangBogo":
        return f"AQ0{cam}_{date_str}_FTP.txt"
    return f"{date_str}_{site_name}_cam{cam}.txt"


def _detect_cams(date_str: str, site_name: str) -> List[str]:
    obs_dir = OBS_ROOT / date_str / site_name
    if not obs_dir.exists():
        return []
    candidates = [str(i) for i in range(1, 8)] + [str(i) for i in range(101, 112)]
    return [c for c in candidates
            if (obs_dir / _raw_filename(site_name, date_str, c)).exists()]


# =============================================================================
# FTP 파싱
# =============================================================================
def _split_ftp_into_blocks(file_path: str) -> List[List[str]]:
    """FTP 텍스트 파일을 streak block (list[str]) 단위로 분리."""
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = [ln.strip() for ln in f if ln.strip()]

    blocks: List[List[str]] = []
    cur: List[str] = []
    for line in lines:
        if line.startswith("FF_") and line.endswith(".fits"):
            if cur:
                blocks.append(cur)
            cur = [line]
        elif line.startswith("-----"):
            if cur:
                blocks.append(cur)
                cur = []
        else:
            cur.append(line)
    if cur:
        blocks.append(cur)
    return blocks


def _parse_block(block_lines: List[str]) -> Optional[pd.DataFrame]:
    """
    단일 streak block → DataFrame(time, ra, dec, inten, name) 또는 None.
    토큰 순서: frame_no, Col, Row, RA, Dec, Azim, Elev, Inten, Mag, ...
    """
    if len(block_lines) < 4:
        return None

    try:
        fits_name = block_lines[0].replace(".fits", "")
        parts = fits_name.split("_")
        base_dt = datetime.strptime(parts[2] + parts[3] + parts[4],
                                    "%Y%m%d%H%M%S%f")
    except Exception:
        return None

    fpx: Optional[float] = None
    streak_num = "0001"
    for line in block_lines:
        toks = line.split()
        if (len(toks) >= 4 and len(toks[0]) >= 2
                and toks[0][:2].isalpha() and toks[0][:2].isupper()):
            try:
                fpx = float(toks[3])
                streak_num = str(toks[1])
                break
            except (ValueError, IndexError):
                continue
    if not fpx or fpx <= 0:
        return None

    rows = []
    for line in block_lines[3:]:
        toks = line.split()
        if len(toks) < 9:
            continue
        try:
            rows.append({
                "time":  base_dt + timedelta(seconds=float(toks[0]) / fpx),
                "ra":    float(toks[3]),
                "dec":   float(toks[4]),
                "inten": float(toks[7]),
            })
        except (ValueError, IndexError):
            continue
    if not rows:
        return None

    df = pd.DataFrame(rows)
    df["name"] = f"{fits_name}_{streak_num}"
    return df


# =============================================================================
# RA/Dec LS fit (자유 시각 평가용)
# =============================================================================
def _fit_ra_dec_ls(df_raw: pd.DataFrame, degree: int = 1):
    """raw (time, ra, dec) → (t0, ra_fn(t_sec), dec_fn(t_sec))."""
    df = df_raw.sort_values("time").reset_index(drop=True)
    t0 = df["time"].iloc[0]
    t_sec = (df["time"] - t0).dt.total_seconds().to_numpy(dtype=float)

    ra_unw = np.rad2deg(np.unwrap(np.deg2rad(df["ra"].to_numpy(dtype=float))))
    dec = df["dec"].to_numpy(dtype=float)

    def _fit(y):
        m = np.isfinite(t_sec) & np.isfinite(y)
        if m.sum() == 0:
            return np.array([0.0])
        deg = min(degree, max(0, m.sum() - 1))
        coef, *_ = np.linalg.lstsq(
            np.vander(t_sec[m], N=deg + 1, increasing=True),
            y[m], rcond=None,
        )
        return coef

    ra_coef = _fit(ra_unw)
    dec_coef = _fit(dec)

    def _eval(coef, t):
        t = np.asarray(t, dtype=float)
        out = np.zeros_like(t, dtype=float)
        p = np.ones_like(t, dtype=float)
        for c in coef:
            out += c * p
            p *= t
        return out

    ra_fn = lambda t: np.mod(_eval(ra_coef, t), 360.0)  # noqa: E731
    dec_fn = lambda t: _eval(dec_coef, t)                # noqa: E731
    return t0, ra_fn, dec_fn


# =============================================================================
# Motion (center ±1초 두 점)
# =============================================================================
def _compute_motion(t_center_sec: float, ra_fn, dec_fn):
    """center ±1초 두 점 → (speed deg/s, PA deg [0,360))."""
    ra1, dec1 = float(ra_fn(t_center_sec - 1.0)), float(dec_fn(t_center_sec - 1.0))
    ra2, dec2 = float(ra_fn(t_center_sec + 1.0)), float(dec_fn(t_center_sec + 1.0))
    ra1r, dec1r = math.radians(ra1), math.radians(dec1)
    ra2r, dec2r = math.radians(ra2), math.radians(dec2)

    cos_theta = max(-1.0, min(1.0,
        math.sin(dec1r) * math.sin(dec2r)
        + math.cos(dec1r) * math.cos(dec2r) * math.cos(ra1r - ra2r)
    ))
    omega = math.degrees(math.acos(cos_theta)) / 2.0

    dra = ra2r - ra1r
    pa = math.degrees(math.atan2(
        math.sin(dra),
        math.cos(dec1r) * math.tan(dec2r) - math.sin(dec1r) * math.cos(dra),
    ))
    return omega, pa % 360.0


# =============================================================================
# Mean intensity [t-0.5, t+0.5)
# =============================================================================
def _mean_intensity_window(times_sec: np.ndarray, inten: np.ndarray,
                            t_sec: float) -> float:
    m = (times_sec >= t_sec - 0.5) & (times_sec < t_sec + 0.5) & np.isfinite(inten)
    if not m.any():
        return float("nan")
    return float(np.mean(inten[m]))


# =============================================================================
# streak 하나 → 출력 라인들
# =============================================================================
def _streak_to_lines(block_lines: List[str]) -> Optional[List[str]]:
    df = _parse_block(block_lines)
    if df is None or len(df) < MIN_N_OBS:
        return None

    df = df.sort_values("time").reset_index(drop=True)
    duration_s = (df["time"].iloc[-1] - df["time"].iloc[0]).total_seconds()
    if duration_s < MIN_DURATION_S:
        return None

    try:
        t0, ra_fn, dec_fn = _fit_ra_dec_ls(df)
    except Exception:
        return None

    raw_t = (df["time"] - t0).dt.total_seconds().to_numpy(dtype=float)
    raw_inten = df["inten"].to_numpy(dtype=float)

    # center 기준 ±1초 grid (대칭, 홀수 N)
    t_center_sec = duration_s / 2.0
    K = int(math.floor(min(t_center_sec, duration_s - t_center_sec)))
    if K < 1:
        return None
    N = 2 * K + 1
    grid_sec = t_center_sec + np.arange(-K, K + 1, dtype=float)

    ra_c = float(ra_fn(t_center_sec))
    dec_c = float(dec_fn(t_center_sec))
    t_center = t0 + pd.Timedelta(seconds=t_center_sec)

    try:
        speed, pa = _compute_motion(t_center_sec, ra_fn, dec_fn)
    except Exception:
        return None

    streak_id = str(df["name"].iloc[0])

    def _fmt_time(ts):
        return pd.Timestamp(ts).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]

    header = (
        f"{streak_id}  {N}  {_fmt_time(t_center)}  "
        f"{ra_c:.6f}  {dec_c:.6f}  {pa:.4f}  {speed:.6f}"
    )

    body = []
    for tg in grid_sec:
        t_abs = t0 + pd.Timedelta(seconds=float(tg))
        ra_g = float(ra_fn(tg))
        dec_g = float(dec_fn(tg))
        inten_g = _mean_intensity_window(raw_t, raw_inten, float(tg))
        inten_str = f"{inten_g:.2f}" if math.isfinite(inten_g) else "NaN"
        body.append(f"{_fmt_time(t_abs)}  {ra_g:.6f}  {dec_g:.6f}  {inten_str}")

    return [header] + body


# =============================================================================
# 한 cam 처리
# =============================================================================
def _process_one_cam(date_str: str, site_name: str, cam: str):
    raw_path = OBS_ROOT / date_str / site_name / _raw_filename(site_name, date_str, cam)
    if not raw_path.exists():
        return 0, []

    blocks = _split_ftp_into_blocks(str(raw_path))[4:]  # 헤더 4블록 제외
    out_blocks = []
    for block in blocks:
        lines = _streak_to_lines(block)
        if lines is not None:
            out_blocks.append(lines)
    return len(blocks), out_blocks


# =============================================================================
# 공개 API
# =============================================================================
def run(date_str: str, site, cams: Optional[List[str]] = None) -> Path:
    """
    한 (date, site) 의 모든 cam FTP → 단일 _str.txt 생성.

    Parameters
    ----------
    date_str : "YYYYMMDD"
    site     : Site (classes.sites.Site) 또는 site.name 만 있어도 OK
    cams     : 없으면 OBS_ROOT 에서 자동 감지

    Returns
    -------
    Path  : 생성된 _str.txt 경로
    """
    site_name = site.name if hasattr(site, "name") else str(site)
    if not cams:
        cams = _detect_cams(date_str, site_name)
    if not cams:
        raise FileNotFoundError(
            f"No FTP files under {OBS_ROOT / date_str / site_name}"
        )

    out_dir = OUT_ROOT / date_str / site_name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{date_str}_{site_name}_str.txt"

    print()
    print("=== Step 1: make_str ===")
    print(f"  date       : {date_str}")
    print(f"  site       : {site_name}")
    print(f"  cams       : {cams}")
    print(f"  pre-filter : duration >= {MIN_DURATION_S}s AND n_obs >= {MIN_N_OBS}")
    print(f"  output     : {out_path}")

    g_total = g_written = 0
    t_start = time.time()

    with open(out_path, "w", encoding="utf-8") as fout:
        for cam in cams:
            t_c = time.time()
            n_total, out_blocks = _process_one_cam(date_str, site_name, cam)
            n_written = len(out_blocks)
            for lines in out_blocks:
                for ln in lines:
                    fout.write(ln + "\n")
                fout.write(STREAK_SEP + "\n")
            g_total += n_total
            g_written += n_written
            pct = (n_written / n_total * 100.0) if n_total else 0.0
            print(f"  [cam{cam}] {n_written}/{n_total} kept ({pct:.1f}%)  "
                  f"{time.time()-t_c:.1f}s")

    overall = (g_written / g_total * 100.0) if g_total else 0.0
    print(f"  TOTAL      : {g_written}/{g_total} kept ({overall:.1f}%)  "
          f"in {time.time()-t_start:.1f}s")
    return out_path