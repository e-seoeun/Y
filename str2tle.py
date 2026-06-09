"""
str2tle.py
-----------
TLE 목록 매칭.

현재 폴더(CWD)의 고정 입력 파일을 읽어 고정 출력 파일을 만든다.
경로/날짜/사이트 등 어떤 외부 정보도 가정하지 않는다.

  input  :  ./site.txt      (lat, long, elevation)
            ./str_p.txt     (각 streak 의 property line; ftp2str 의 str.txt line1 들)
            ./catalog.txt   (published / classified TLE, 또는 관심 TLE 1개. 객체당 3줄)
  output :  ./str_m.txt

str_p.txt 의 각 line 에 매칭결과를 덧붙인다:
  streak_id  N  time  RA_center  Dec_center  MotionAngle  Speed
    + no_match
    + norad_id  d1  d2  da  dv

  - d1 / d2 : observation vs candidate 선분의 normal / parallel 거리 (deg)
  - da      : position angle 차이 (deg)
  - dv      : 각속도 상대오차 (|w_cand - w_obs| / |w_obs|)

str_p.txt 에는 1초 grid body 가 없으므로, d1/d2 계산에 필요한 center ±1초 두 점은
header 의 (RA_center, Dec_center, MotionAngle, Speed) 로부터 great-circle 상에서
해석적으로 복원한다 (ftp2str 의 _compute_motion 역연산).

공개 API:
  run(...) -> Path     # str_m.txt 경로
"""

from __future__ import annotations

import math
import multiprocessing as mp
import os
import re
import sys
import time as wall_time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import List

import pandas as pd

from candidate_search import build_orbit_cache, search_candidates
from motion import compute_candidate_motion
from evaluate import (
    compute_d1_d2,
    evaluate_stage1, evaluate_stage2,
    pa_diff_deg,
)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, Exception):
    pass


# 고정 입출력 파일명 (현재 폴더 기준)
SITE_FILE = Path("site.txt")
STR_FILE = Path("str_p.txt")       # 1순위 입력 (있으면 사용)
STR_FALLBACK = Path("str.txt")     # 없으면 ftp2str 출력(str.txt)을 바로 사용
CATALOG_FILE = Path("catalog.txt")
OUT_FILE = Path("str_m.txt")

# 식별 파라미터 — 필요 시 조정
SEARCH_RADIUS_DEG = 5.0                                 # 후보 cone 반경 [deg]
N_WORKERS = max(1, (os.cpu_count() or 2) // 2 - 1)      # 병렬 워커 수

# 출력 컬럼 폭
W_NAME, W_N, W_TIME = 45, 3, 23
W_RA, W_DEC = 11, 11
W_PA, W_SP = 9, 10
W_NORAD, W_DD = 8, 8


# =============================================================================
# 관측소 위치 읽기 (site.txt 또는 *_SUMMARY.txt)
# =============================================================================
def read_site(path: Path):
    """
    위치 파일 → SimpleNamespace(lat_deg, lon_deg, alt_m).  elevation 단위는 미터.

    1) 라벨이 있으면 라벨로 읽는다 (순서 무관). *_SUMMARY.txt 의
         '"elev": 44.0  "lat": 34.526  "lon": 127.447' 형식이 여기에 해당.
       (lat/latitude, lon/lng/long/longitude, elev/elevation/altitude/height;
        ':' '=' ',' '"' 구분 허용. 단어경계로 'camera_pointing_alt' 같은 건 제외.)
    2) 라벨이 하나도 없으면 → 처음 등장하는 숫자 3개를 lat, lon, elev 순으로.
    """
    import re
    if not path.exists():
        raise FileNotFoundError(f"위치 파일이 없습니다: {path.resolve()}")

    text = open(path, "r", encoding="utf-8", errors="ignore").read()
    num = r"[-+]?\d+\.?\d*(?:[eE][-+]?\d+)?"
    sep = r"[\s:=\"',]*"  # 라벨과 숫자 사이 허용 구분자 (JSON/콤마/등호 등)

    def _find(label_pat):
        m = re.search(rf"\b(?:{label_pat})\b{sep}({num})", text, re.IGNORECASE)
        return float(m.group(1)) if m else None

    lat = _find(r"lat(?:itude)?")
    lon = _find(r"lon(?:gitude)?|lng|long")
    elev = _find(r"elev(?:ation)?|altitude|height")  # 'alt' 단독은 제외(오인 방지)

    if lat is not None and lon is not None and elev is not None:
        return SimpleNamespace(name="site", lat_deg=lat, lon_deg=lon, alt_m=elev)

    # 라벨이 없으면 위치 기반 (lat, lon, elev 순)
    nums = [float(t) for t in re.findall(num, text)]
    if len(nums) < 3:
        raise ValueError(
            f"위치 파일에서 lat, lon, elevation 값을 찾지 못했습니다: {path.resolve()}"
        )
    return SimpleNamespace(name="site", lat_deg=nums[0], lon_deg=nums[1], alt_m=nums[2])


def resolve_site(folder: Path) -> Path:
    """
    관측소 위치 파일을 결정한다.
      1) site.txt 가 있으면 → 그것 (사용자가 명시적으로 둔 경우)
      2) 없으면 → 이름에 'SUMMARY' 가 든 파일 (예: 20260501_GoHeung_SUMMARY.txt)
                  대소문자/확장자 무관. 여러 개면 정렬상 첫 번째.
      3) 하나도 없으면 → FileNotFoundError
    """
    if SITE_FILE.exists():
        return SITE_FILE
    summaries = sorted(p for p in folder.iterdir()
                       if p.is_file() and "summary" in p.name.lower())
    if not summaries:
        raise FileNotFoundError(
            f"위치 정보를 찾지 못했습니다: '{SITE_FILE.name}' 또는 '*SUMMARY*' 파일 "
            f"({folder})"
        )
    return summaries[0]


# =============================================================================
# catalog(TLE) 파일 자동 감지
# =============================================================================
def _date_in_path(folder: Path):
    """경로 구성요소에서 YYYYMMDD 를 찾는다 (예: .../20260501/GoHeung → '20260501')."""
    for part in [folder.name] + [p.name for p in folder.parents]:
        if re.fullmatch(r"\d{8}", part):
            return part
    return None


def _nearby_tle_dir(folder: Path):
    """현재 폴더에서 위로 올라가며 'TLE' 디렉토리를 찾는다 (예: YSPACE/TLE)."""
    for base in [folder, *folder.parents]:
        cand = base / "TLE"
        if cand.is_dir():
            return cand
    return None


def resolve_catalog(folder: Path) -> Path:
    """
    catalog(TLE) 파일을 결정한다.
      1) 현재 폴더의 catalog.txt 가 있으면 → 그것
      2) 현재 폴더의 '*catalog.txt' 가 있으면 → 그것 (여러 개면 정렬상 첫 번째)
      3) 위 둘 다 없으면 → 상위에서 'TLE' 폴더를 찾아, 그 폴더 경로의 날짜(YYYYMMDD)에
         해당하는 'YYYYMMDD_*_catalog.txt' 중 가장 이른 시간(이름순 첫 번째)을 쓴다.
         (예: YSPACE/YYYYMMDD/site 에서 실행 → YSPACE/TLE 의 그날 가장 이른 catalog)
      4) 그래도 없으면 → FileNotFoundError
    """
    if CATALOG_FILE.exists():
        return CATALOG_FILE

    matches = sorted(p for p in folder.glob("*catalog.txt") if p.is_file())
    if matches:
        if len(matches) > 1:
            print(f"  [주의] catalog 후보 {len(matches)}개 중 '{matches[0].name}' 사용 "
                  f"(전체: {', '.join(p.name for p in matches)})")
        return matches[0]

    # 폴백: 인근 TLE 폴더에서 그날 가장 이른 catalog
    tle_dir = _nearby_tle_dir(folder)
    date = _date_in_path(folder)
    if tle_dir is not None and date is not None:
        day = sorted(tle_dir.glob(f"{date}_*_catalog.txt"))
        if day:
            print(f"  [auto] catalog : {day[0].name}  (from {tle_dir})")
            return day[0]

    raise FileNotFoundError(
        f"catalog(TLE) 를 찾지 못했습니다: 현재 폴더의 'catalog.txt'/'*catalog.txt' "
        f"또는 인근 TLE 폴더의 '{date or 'YYYYMMDD'}_*_catalog.txt' ({folder})"
    )


# =============================================================================
# str_p.txt 파싱 (streak property line 들)
# =============================================================================
def _parse_time_token(s: str) -> datetime:
    return datetime.fromisoformat(s)


def parse_str_p_file(path: Path) -> List[dict]:
    """
    str_p.txt → list[streak dict].

    각 streak property line:
        streak_id  N  time  RA_center  Dec_center  MotionAngle  Speed

    body line(4토큰)·구분자·주석은 무시하므로, line1 만 추출한 str_p.txt 든
    body 가 남아있는 str.txt 든 동일하게 처리된다.
    """
    streaks: List[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            ln = raw.strip()
            if not ln or ln.startswith("#") or ln.startswith("-"):
                continue
            toks = ln.split()
            if len(toks) < 7:
                continue
            try:
                streaks.append({
                    "streak_id":  toks[0],
                    "N":          int(toks[1]),
                    "t_center":   _parse_time_token(toks[2]),
                    "ra_center":  float(toks[3]),
                    "dec_center": float(toks[4]),
                    "pa_obs":     float(toks[5]),
                    "w_obs":      float(toks[6]),
                })
            except (ValueError, IndexError):
                continue
    return streaks


# =============================================================================
# center + PA + speed → center ±1초 두 점 (great-circle destination)
# =============================================================================
def _dest_point(ra0_deg, dec0_deg, pa_deg, dist_deg):
    """center 에서 bearing(N→E) pa, 각거리 dist 떨어진 점의 (ra, dec) [deg]."""
    ra0 = math.radians(ra0_deg)
    dec0 = math.radians(dec0_deg)
    pa = math.radians(pa_deg)
    d = math.radians(dist_deg)

    dec1 = math.asin(
        math.sin(dec0) * math.cos(d) + math.cos(dec0) * math.sin(d) * math.cos(pa)
    )
    ra1 = ra0 + math.atan2(
        math.sin(pa) * math.sin(d) * math.cos(dec0),
        math.cos(d) - math.sin(dec0) * math.sin(dec1),
    )
    return math.degrees(ra1) % 360.0, math.degrees(dec1)


def _obs_rows(streak):
    """
    header (center, PA, speed) → (row_mid, row_before, row_after).
    after  : center 에서 bearing=PA,      거리=speed(=deg/s × 1s)
    before : center 에서 bearing=PA+180,  거리=speed
    """
    ra0 = streak["ra_center"]
    dec0 = streak["dec_center"]
    pa = streak["pa_obs"]
    w = streak["w_obs"]
    t_mid = streak["t_center"]

    ra_a, dec_a = _dest_point(ra0, dec0, pa, w)
    ra_b, dec_b = _dest_point(ra0, dec0, pa + 180.0, w)

    return (
        {"time": pd.Timestamp(t_mid),                              "ra_ls": ra0,  "dec_ls": dec0},
        {"time": pd.Timestamp(t_mid - pd.Timedelta(seconds=1)),   "ra_ls": ra_b, "dec_ls": dec_b},
        {"time": pd.Timestamp(t_mid + pd.Timedelta(seconds=1)),   "ra_ls": ra_a, "dec_ls": dec_a},
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
# 진행 바 (한 줄, \r 로 갱신)
# =============================================================================
_BAR_W = 30


def _draw_progress(n_done: int, n_total: int, n_matched: int, elapsed: float):
    frac = (n_done / n_total) if n_total else 1.0
    filled = int(_BAR_W * frac)
    bar = "█" * filled + "░" * (_BAR_W - filled)
    eta = elapsed / n_done * (n_total - n_done) if n_done else 0.0
    line = (f"  [{bar}] {frac * 100:5.1f}%  {n_done}/{n_total}  "
            f"matched={n_matched}  ETA {eta:.0f}s")
    sys.stdout.write("\r" + line.ljust(76))
    sys.stdout.flush()


# =============================================================================
# 공개 API
# =============================================================================
def run(site_path=None, str_path=None,
        catalog_path=None, out_path=OUT_FILE) -> Path:
    """
    (./site.txt 또는 *SUMMARY*) + (./str_p.txt 또는 ./str.txt)
        + (./catalog.txt 또는 *catalog.txt) → ./str_m.txt 생성.

    각 입력은 미지정 시 자동으로 결정된다:
      - 위치   : site.txt 가 있으면 그것, 없으면 *_SUMMARY.txt 에서 lat/lon/elev 추출
      - streak : str_p.txt 가 있으면 그것, 없으면 str.txt (header line1 만 읽음)
      - TLE    : catalog.txt 가 있으면 그것, 없으면 *catalog.txt
    입력이 하나라도 없으면 FileNotFoundError.
    """
    folder = Path.cwd()
    out_path = Path(out_path)

    if str_path is not None:
        str_path = Path(str_path)
        if not str_path.exists():
            raise FileNotFoundError(f"streak 파일이 현재 폴더에 없습니다: {str_path.resolve()}")
    elif STR_FILE.exists():
        str_path = STR_FILE
    elif STR_FALLBACK.exists():
        str_path = STR_FALLBACK
    else:
        raise FileNotFoundError(
            f"streak 파일이 현재 폴더에 없습니다: {STR_FILE.name} 또는 {STR_FALLBACK.name} "
            f"({Path.cwd()})"
        )
    if catalog_path is not None:
        catalog_path = Path(catalog_path)
        if not catalog_path.exists():
            raise FileNotFoundError(f"catalog 파일이 현재 폴더에 없습니다: {catalog_path.resolve()}")
    else:
        catalog_path = resolve_catalog(folder)  # catalog.txt 또는 *catalog.txt

    if site_path is not None:
        site_path = Path(site_path)
    else:
        site_path = resolve_site(folder)  # site.txt 또는 *SUMMARY*
    site = read_site(site_path)

    print()
    print("=== str2tle ===")
    print(f"  site     : lat={site.lat_deg}, lon={site.lon_deg}, alt={site.alt_m} m "
          f"(from {site_path.name})")
    print(f"  input    : {str_path}")
    print(f"  catalog  : {catalog_path}")
    print(f"  output   : {out_path}")

    streaks = parse_str_p_file(str_path)
    n_total = len(streaks)
    print(f"  streaks  : {n_total}")
    if n_total == 0:
        with open(out_path, "w", encoding="utf-8") as fout:
            fout.write(_format_header() + "\n")
        return out_path

    print("  pre-check orbit cache ...")
    tle_abs = str(catalog_path.resolve())
    _main_cache = build_orbit_cache([tle_abs])
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
        initargs=(tle_abs, site_kwargs),
    ) as pool:
        for r in pool.imap_unordered(_worker_process, streaks, chunksize=4):
            results.append(r)
            n_done += 1
            if r["norad"] != "no_match":
                n_matched += 1
            _draw_progress(n_done, n_total, n_matched,
                           wall_time.time() - t_start)
    sys.stdout.write("\n")  # 진행 바 줄 마무리

    results.sort(key=lambda x: x["time"])
    with open(out_path, "w", encoding="utf-8") as fout:
        fout.write(_format_header() + "\n")
        for r in results:
            fout.write(_format_row(r) + "\n")

    print(f"  DONE     : matched={n_matched}/{n_total}, "
          f"no_match={n_total - n_matched}, "
          f"total={wall_time.time() - t_start:.1f}s")
    return out_path


if __name__ == "__main__":
    run()
