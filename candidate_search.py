"""
candidate_search.py
-------------------
TLE 카탈로그를 SGP4 로 전파해서 주어진 시각/방향(cone) 안에 들어오는
위성 후보를 찾는 모듈.

핵심 API:
    build_orbit_cache(tle_paths)  -> OrbitCache
    search_candidates(orbit_cache, site, time_iso, ra0_deg, de0_deg,
                      radius_deg, offsets_s=(-1,0,+1), use_visibility=True)
        -> list[dict]   (각 dict: rso, cospar, mjd, ra_deg, dec_deg,
                         age_days, AngDst_deg, TfC_s, state)

`site` 는 lat_deg / lon_deg / alt_km 속성을 가진 어떤 객체든 OK
(SimpleNamespace, classes.sites.Site, dataclass 등 모두 가능).
"""

import math
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

import numpy as np
from sgp4.api import Satrec, SatrecArray


# =============================================================================
# 상수
# =============================================================================
D2R = math.pi / 180.0
R2D = 180.0 / math.pi
XKMPER = 6378.135               # WGS-72
XKMPAU = 149597879.691
FLAT = 1.0 / 298.257
TWOPI = 2.0 * math.pi


# =============================================================================
# 시간 변환
# =============================================================================
def date2mjd(year: int, month: int, day: float) -> float:
    if month < 3:
        year -= 1
        month += 12
    a = math.floor(year / 100.0)
    b = 2.0 - a + math.floor(a / 4.0)
    if year < 1582:
        b = 0.0
    if year == 1582 and month < 10:
        b = 0.0
    if year == 1582 and month == 10 and day <= 4:
        b = 0.0
    jd = (math.floor(365.25 * (year + 4716))
          + math.floor(30.6001 * (month + 1))
          + day + b - 1524.5)
    return jd - 2400000.5


def nfd2mjd(date_str: str) -> float:
    """ISO-like 'YYYY-MM-DDTHH:MM:SS.fff' → MJD."""
    y = int(date_str[0:4])
    mo = int(date_str[5:7])
    d = int(date_str[8:10])
    hh = int(date_str[11:13])
    mm = int(date_str[14:16])
    sec_f32 = np.float32(date_str[17:])
    sec = float(sec_f32)
    dday = d + hh / 24.0 + mm / 1440.0 + sec / 86400.0
    return date2mjd(y, mo, dday)


def _mod(x: float, y: float) -> float:
    x = math.fmod(x, y)
    return x + y if x < 0.0 else x


# =============================================================================
# 천문 보조함수
# =============================================================================
def gmst_deg(mjd: float) -> float:
    t = (mjd - 51544.5) / 36525.0
    g = (280.46061837
         + 360.98564736629 * (mjd - 51544.5)
         + t * t * (0.000387933 - t / 38710000.0))
    return _mod(g, 360.0)


def obspos_xyz_vec(mjds: np.ndarray, site) -> np.ndarray:
    """관측자 위치 (ECI-of-date, km). mjds: (M,) -> (M, 3)."""
    lat_deg = float(site.lat_deg)
    lon_deg = float(site.lon_deg)
    alt_km = float(site.alt_km)

    s = math.sin(lat_deg * D2R)
    ff = math.sqrt(1.0 - FLAT * (2.0 - FLAT) * s * s)
    gc = 1.0 / ff + alt_km / XKMPER
    gs = (1.0 - FLAT) ** 2 / ff + alt_km / XKMPER

    coslat = math.cos(lat_deg * D2R)
    sinlat = math.sin(lat_deg * D2R)

    g = np.array([gmst_deg(float(m)) for m in mjds], dtype=float) + lon_deg
    th = np.deg2rad(g)
    return np.column_stack([
        gc * coslat * np.cos(th) * XKMPER,
        gc * coslat * np.sin(th) * XKMPER,
        np.full_like(th, gs * sinlat * XKMPER),
    ])


@dataclass
class Vec3:
    x: float
    y: float
    z: float


def sunpos_xyz(mjd: float) -> Vec3:
    jd = mjd + 2400000.5
    t = (jd - 2451545.0) / 36525.0
    l0 = _mod(280.46646 + t * (36000.76983 + t * 0.0003032), 360.0) * D2R
    m = _mod(357.52911 + t * (35999.05029 - t * 0.0001537), 360.0) * D2R
    e = 0.016708634 + t * (-0.000042037 - t * 0.0000001267)
    c = (1.914602 + t * (-0.004817 - t * 0.000014)) * math.sin(m) * D2R
    c += (0.019993 - 0.000101 * t) * math.sin(2.0 * m) * D2R
    c += 0.000289 * math.sin(3.0 * m) * D2R
    r = 1.000001018 * (1.0 - e * e) / (1.0 + e * math.cos(m + c))
    n = _mod(125.04 - 1934.136 * t, 360.0) * D2R
    s = l0 + c + (-0.00569 - 0.00478 * math.sin(n)) * D2R
    ecl = (23.43929111
           + (-46.8150 * t - 0.00059 * t * t + 0.001813 * t * t * t) / 3600.0
           + 0.00256 * math.cos(n)) * D2R
    ra = math.atan2(math.cos(ecl) * math.sin(s), math.cos(s))
    de = math.asin(math.sin(ecl) * math.sin(s))
    return Vec3(
        x=r * math.cos(de) * math.cos(ra) * XKMPAU,
        y=r * math.cos(de) * math.sin(ra) * XKMPAU,
        z=r * math.sin(de) * XKMPAU,
    )


def precession_angles_vec(mjds: np.ndarray, mjd_ref: float = 51544.5) -> np.ndarray:
    mjds = np.asarray(mjds, dtype=float)
    t0 = (mjds - 51544.5) / 36525.0
    t = (mjd_ref - mjds) / 36525.0

    zeta = (2306.2181 + 1.39656 * t0 - 0.000139 * t0**2) * t
    zeta += (0.30188 - 0.000344 * t0) * t**2 + 0.017998 * t**3
    zeta *= D2R / 3600.0

    z = (2306.2181 + 1.39656 * t0 - 0.000139 * t0**2) * t
    z += (1.09468 + 0.000066 * t0) * t**2 + 0.018203 * t**3
    z *= D2R / 3600.0

    theta = (2004.3109 - 0.85330 * t0 - 0.000217 * t0**2) * t
    theta += -(0.42665 + 0.000217 * t0) * t**2 - 0.041833 * t**3
    theta *= D2R / 3600.0

    return np.column_stack([zeta, z, theta])


# =============================================================================
# TLE 파싱 / OrbitCache
# =============================================================================
def iter_tles(path: str) -> Iterable[Tuple[str, str, Optional[str]]]:
    """TLE 파일 → (line1, line2, name_or_None) 이터레이터. 2-line / 3-line 모두 허용."""
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        lines = [ln.rstrip("\n") for ln in f if ln.strip()]
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        if (ln.startswith("1 ")
                and i + 1 < len(lines)
                and lines[i + 1].lstrip().startswith("2 ")):
            yield ln, lines[i + 1].strip(), None
            i += 2
            continue
        if (i + 2 < len(lines)
                and lines[i + 1].lstrip().startswith("1 ")
                and lines[i + 2].lstrip().startswith("2 ")):
            name = lines[i].strip()
            if name.startswith("0 "):
                name = name[2:].lstrip()
            yield lines[i + 1].strip(), lines[i + 2].strip(), name
            i += 3
            continue
        i += 1


@dataclass
class OrbitCache:
    sats: List[Satrec]
    sat_array: Optional[SatrecArray]
    cospar: np.ndarray
    satnum: np.ndarray
    epoch_jd: np.ndarray

    def __len__(self) -> int:
        return len(self.sats)


def build_orbit_cache(tle_paths: List[str], skip_deep_space: bool = True) -> OrbitCache:
    """여러 TLE 파일을 읽어 SatrecArray 까지 만든 OrbitCache 반환."""
    sats: List[Satrec] = []
    cospars: List[str] = []
    for tle_path in tle_paths:
        for l1, l2, name in iter_tles(tle_path):
            try:
                sat = Satrec.twoline2rv(l1, l2)
            except Exception:
                continue
            if skip_deep_space and sat.method == "d":
                continue
            sats.append(sat)
            cospars.append((name or "").strip())

    if not sats:
        return OrbitCache(
            sats=[], sat_array=None,
            cospar=np.array([], dtype=object),
            satnum=np.array([], dtype=np.int64),
            epoch_jd=np.array([], dtype=np.float64),
        )

    return OrbitCache(
        sats=sats,
        sat_array=SatrecArray(sats),
        cospar=np.array(cospars, dtype=object),
        satnum=np.array([s.satnum for s in sats], dtype=np.int64),
        epoch_jd=np.array([s.jdsatepoch + s.jdsatepochF for s in sats], dtype=np.float64),
    )


# =============================================================================
# 작은 헬퍼
# =============================================================================
def _radec_to_unit(ra_deg: float, dec_deg: float) -> Tuple[float, float, float]:
    ra = ra_deg * D2R
    de = dec_deg * D2R
    c = math.cos(de)
    return (c * math.cos(ra), c * math.sin(ra), math.sin(de))


def _classify_visibility(sat: Vec3, sun: Vec3) -> str:
    dx, dy, dz = -sat.x + sun.x, -sat.y + sun.y, -sat.z + sun.z
    rsun = math.sqrt(dx * dx + dy * dy + dz * dz)
    rearth = math.sqrt(sat.x ** 2 + sat.y ** 2 + sat.z ** 2)
    psun = math.asin(696.0e3 / rsun) * R2D
    pearth = math.asin(6378.135 / rearth) * R2D
    ptot = math.acos((-dx * sat.x - dy * sat.y - dz * sat.z) / (rsun * rearth)) * R2D
    x = ptot - pearth
    if x < -psun:
        return "eclipsed"
    elif -psun <= x <= psun:
        return "umbra"
    return "sunlit"


def _radec_from_topo_and_prec_vec(topo, prec):
    dx, dy, dz = topo[..., 0], topo[..., 1], topo[..., 2]
    rng = np.sqrt(dx * dx + dy * dy + dz * dz)
    valid = rng > 0.0
    rng_safe = np.where(valid, rng, 1.0)
    ra = np.mod(np.arctan2(dy, dx), TWOPI)
    de = np.arcsin(np.clip(dz / rng_safe, -1.0, 1.0))

    zeta = prec[np.newaxis, :, 0]
    z_p = prec[np.newaxis, :, 1]
    theta = prec[np.newaxis, :, 2]

    cos_de, sin_de = np.cos(de), np.sin(de)
    cos_rz, sin_rz = np.cos(ra + zeta), np.sin(ra + zeta)
    cos_th, sin_th = np.cos(theta), np.sin(theta)

    a = cos_de * sin_rz
    b = cos_th * cos_de * cos_rz - sin_th * sin_de
    c_ = sin_th * cos_de * cos_rz + cos_th * sin_de

    ra_deg = np.rad2deg(np.arctan2(a, b) + z_p) % 360.0
    dec_deg = np.rad2deg(np.arcsin(np.clip(c_, -1.0, 1.0)))
    return ra_deg, dec_deg, valid


# =============================================================================
# 후보 탐색 (메인 API)
# =============================================================================
def search_candidates(
    orbit_cache: OrbitCache,
    site,
    time_iso: str,
    ra0_deg: float,
    de0_deg: float,
    radius_deg: float,
    offsets_s: Tuple[float, ...] = (-1.0, 0.0, +1.0),
    use_visibility: bool = True,
) -> List[dict]:
    """
    `time_iso ± offsets_s` 시각들에서 모든 위성을 일괄 전파한 뒤,
    center(=offset 0) 위치 기준 `radius_deg` cone 안에 들어온 후보만 반환.

    반환 dict 키:
        rso, cospar, mjd, ra_deg, dec_deg,
        age_days, AngDst_deg, TfC_s, state
    """
    if (orbit_cache is None or orbit_cache.sat_array is None
            or len(orbit_cache.sats) == 0):
        return []

    mjd0 = nfd2mjd(time_iso)
    offsets_arr = np.asarray(offsets_s, dtype=float)
    mjds = mjd0 + offsets_arr / 86400.0
    jds_full = mjds + 2400000.5
    jd_arr = np.floor(jds_full)
    fr_arr = jds_full - jd_arr

    e_codes, r, _v = orbit_cache.sat_array.sgp4(jd_arr, fr_arr)
    obs_xyz = obspos_xyz_vec(mjds, site)
    prec = precession_angles_vec(mjds)

    topo = r - obs_xyz[np.newaxis, :, :]
    rnorm = np.linalg.norm(r, axis=2)
    ra_deg_arr, dec_deg_arr, valid_rng = _radec_from_topo_and_prec_vec(topo, prec)
    valid = (e_codes == 0) & (rnorm <= 300000.0) & valid_rng

    zero_idx = np.where(offsets_arr == 0.0)[0]
    idx0 = int(zero_idx[0]) if len(zero_idx) > 0 else 0

    u0x, u0y, u0z = _radec_to_unit(ra0_deg, de0_deg)
    cos_de_c = np.cos(np.deg2rad(dec_deg_arr[:, idx0]))
    ux_c = cos_de_c * np.cos(np.deg2rad(ra_deg_arr[:, idx0]))
    uy_c = cos_de_c * np.sin(np.deg2rad(ra_deg_arr[:, idx0]))
    uz_c = np.sin(np.deg2rad(dec_deg_arr[:, idx0]))
    dot_c = ux_c * u0x + uy_c * u0y + uz_c * u0z

    cos_thr = math.cos(radius_deg * D2R)
    cand_mask = valid[:, idx0] & (dot_c >= cos_thr)
    cand_idx = np.where(cand_mask)[0]
    if cand_idx.size == 0:
        return []

    sun_c = sunpos_xyz(float(mjds[idx0])) if use_visibility else None

    results: List[dict] = []
    n_off = len(offsets_arr)
    for i in cand_idx:
        for j in range(n_off):
            if not valid[i, j]:
                continue
            ra = float(ra_deg_arr[i, j])
            dec = float(dec_deg_arr[i, j])
            ux, uy, uz = _radec_to_unit(ra, dec)
            dot = u0x * ux + u0y * uy + u0z * uz
            if dot < cos_thr:
                continue
            angdst = math.degrees(math.acos(max(-1.0, min(1.0, float(dot)))))
            row = dict(
                rso=int(orbit_cache.satnum[i]),
                cospar=str(orbit_cache.cospar[i]),
                mjd=float(mjds[j]),
                ra_deg=ra,
                dec_deg=dec,
                age_days=float(jds_full[j] - orbit_cache.epoch_jd[i]),
                AngDst_deg=angdst,
                TfC_s=float(offsets_arr[j]),
            )
            if j == idx0:
                if use_visibility and sun_c is not None:
                    sp = Vec3(float(r[i, j, 0]), float(r[i, j, 1]), float(r[i, j, 2]))
                    row["state"] = _classify_visibility(sp, sun_c)
                else:
                    row["state"] = ""
            results.append(row)
    return results