"""
ftp2str.py
-----------
FTP file to simple streak.

현재 폴더(CWD)에서 raw FTP 자료를 읽어 고정 출력 파일을 만든다.
경로/날짜/사이트/카메라 등 어떤 외부 정보도 가정하지 않는다.
simulated data, derived TLE 등 임의의 경우에도 동일하게 사용하기 위함.

입력 결정 순서:
  1) ./ftp.txt 가 있으면 → 그 파일만 처리 (고정 파일명, 명세 기준)
  2) 없으면 → 현재 폴더의 *.txt 중 raw FTP 형식('FF_*.fits' 블록 포함)을
              이름과 무관하게 내용으로 자동 감지하여 모두 처리 (str.txt 로 통합)
  3) 하나도 없으면 → 오류

  input  :  ./ftp.txt  또는 자동 감지된 raw FTP *.txt (들)
  output :  ./str.txt

  - extract streak property for TLE matching
  - derive mean data points at 1 sec interval
  - drop noisy streak data not useful for TLE matching

각 streak 출력 포맷:
  line 1   : streak_id  N  time  RA_center  Dec_center  MotionAngle  Speed
  line 2~N : time  RA  Dec  MeanIntensity         (1초 간격 자료)
  ------------------------                        (streak 구분자)

  streak_id : FTP image name + streak number 를 합친 단일 문자열
  N         : 1초 간격 자료의 개수

핵심 정의:
  - center : streak 시작~끝의 중간 시각
  - 1초 grid : center 기준 ±1, ±2, ... 초로 양쪽 대칭. K=floor(min(t_center, dur-t_center))
                → N = 2K+1 (홀수). center 가 grid 정중앙 row.
  - RA/Dec (sample) : raw 관측점에 LS 1차 fit 후 grid 시각에서 평가
  - MeanIntensity   : raw 관측점 중 [t-0.5s, t+0.5s) 안에 있는 inten 의 평균

한 폴더에 여러 카메라 raw FTP 가 같이 있으면 자동 감지 시 모두 통합한다.
(ftp.txt 가 명시돼 있으면 그 1개만 처리.)

공개 API:
  run(in_path=None, out_path="str.txt") -> Path
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

# 고정 입출력 파일명 (현재 폴더 기준)
IN_FILE = Path("ftp.txt")
OUT_FILE = Path("str.txt")

# 사전 필터 (이 기준 미만은 str.txt 에 안 들어감) — 필요 시 조정
MIN_DURATION_S = 2.0
MIN_N_OBS = 25

# FTP 파일 앞부분 헤더 블록 개수 (streak 가 아님)
N_HEADER_BLOCKS = 4

STREAK_SEP = "------------------------"


# =============================================================================
# 입력 파일 자동 감지 (이름 무관, 내용 기반)
# =============================================================================
# 자동 감지 시 입력에서 제외할 출력/보조 파일 이름
_EXCLUDE_NAMES = {"str.txt", "str_p.txt", "str_m.txt", "catalog.txt", "site.txt"}

# 자동 감지 시 건너뛸 확장자 (이미지/동영상/압축/코드/캘리브레이션 등)
_SKIP_EXT = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff",
    ".mp4", ".avi", ".mov", ".mkv",
    ".tgz", ".gz", ".zip", ".tar", ".7z", ".rar", ".bz2", ".xz",
    ".py", ".pyc", ".pyo", ".ipynb", ".so", ".dll", ".exe", ".bin",
    ".fits", ".fit", ".npy", ".npz", ".pdf", ".hwp", ".doc", ".docx",
    ".xls", ".xlsx", ".cal",
}


def _looks_like_ftp(path: Path, max_bytes: int = 262144) -> bool:
    """
    파일 앞부분에 'FF_*.fits' streak 블록 마커가 있으면 raw FTP 로 판단.
    이름/확장자와 무관하게 내용만으로 판정한다 (사이트마다 명명 규칙이 달라도 동작).
    """
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(max_bytes)
    except OSError:
        return False
    for line in head.splitlines():
        s = line.strip()
        if s.startswith("FF_") and s.endswith(".fits"):
            return True
    return False


def _detect_ftp_files(folder: Path) -> List[Path]:
    """
    현재 폴더에서 raw FTP 파일(들)을 이름과 무관하게 내용으로 감지.
    (cam1.txt, AQ0101_..._FTP.txt 등 어떤 이름이든 FTP 형식이면 잡힌다.)
    """
    found = []
    for p in sorted(folder.iterdir()):
        if not p.is_file():
            continue
        if p.name in _EXCLUDE_NAMES or p.suffix.lower() in _SKIP_EXT:
            continue
        if _looks_like_ftp(p):
            found.append(p)
    return found


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
# 공개 API
# =============================================================================
def run(in_path=None, out_path=OUT_FILE) -> Path:
    """
    현재 폴더의 raw FTP 자료 → ./str.txt 생성.

    Parameters
    ----------
    in_path  : 처리할 FTP 파일을 직접 지정 (단일). None 이면:
                 ./ftp.txt 가 있으면 그것만, 없으면 raw FTP *.txt 자동 감지(통합).
    out_path : 출력 streak 파일 (기본 ./str.txt)

    Returns
    -------
    Path : 생성된 str.txt 경로
    """
    out_path = Path(out_path)
    folder = Path.cwd()

    if in_path is not None:
        in_files = [Path(in_path)]
        for f in in_files:
            if not f.exists():
                raise FileNotFoundError(f"입력 파일이 현재 폴더에 없습니다: {f.resolve()}")
    elif IN_FILE.exists():
        in_files = [IN_FILE]
    else:
        in_files = _detect_ftp_files(folder)

    if not in_files:
        raise FileNotFoundError(
            f"입력 FTP 파일을 현재 폴더에서 찾지 못했습니다: {folder}\n"
            f"  → '{IN_FILE.name}' 가 있거나, 'FF_*.fits' 블록을 가진 *.txt 가 있어야 합니다."
        )

    print()
    print("=== ftp2str ===")
    print(f"  input      : {', '.join(p.name for p in in_files)}")
    print(f"  output     : {out_path}")
    print(f"  pre-filter : duration >= {MIN_DURATION_S}s AND n_obs >= {MIN_N_OBS}")

    t_start = time.time()
    g_total = g_written = 0
    with open(out_path, "w", encoding="utf-8") as fout:
        for f in in_files:
            blocks = _split_ftp_into_blocks(str(f))[N_HEADER_BLOCKS:]
            n_total = len(blocks)
            n_written = 0
            for block in blocks:
                lines = _streak_to_lines(block)
                if lines is None:
                    continue
                for ln in lines:
                    fout.write(ln + "\n")
                fout.write(STREAK_SEP + "\n")
                n_written += 1
            g_total += n_total
            g_written += n_written
            if len(in_files) > 1:
                pct = (n_written / n_total * 100.0) if n_total else 0.0
                print(f"  [{f.name}] {n_written}/{n_total} kept ({pct:.1f}%)")

    pct = (g_written / g_total * 100.0) if g_total else 0.0
    print(f"  TOTAL      : {g_written}/{g_total} kept ({pct:.1f}%)  "
          f"in {time.time()-t_start:.1f}s")
    return out_path


if __name__ == "__main__":
    run()
