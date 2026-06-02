"""
classes/sites.py
----------------
관측 사이트 및 카메라 정의의 단일 진실 공급원(single source of truth).

다른 코드(process2_identification, process3_iod, analyze/*)는 이 모듈만 import 해
사용하면 됨. 사이트/카메라 추가·수정은 이 파일 하나만 고치면 전체 파이프라인에
반영된다.

사용 예:
    from classes.sites import get_by_code, prompt_site_and_cameras, SITES
ㅑㅇ
    # 대화형
    site, cams = prompt_site_and_cameras()

    # 직접 조회
    site = get_by_code("SJ")
    print(site.name, site.lat_deg, site.lon_deg, site.alt_m, site.cameras)
    print(site.obs_filename("20260417", "101"))   # 관측 raw 파일명
    print(site.mch_filename("20260417", "101"))   # MCH 파일명
    print(site.resampled_filename("20260417", "101"))

추가/변경:
    SITES 딕셔너리에 새 Site(...) 항목 추가만 하면 끝.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class Site:
    code: str               # "G", "SJ" 같은 짧은 코드
    name: str               # "GoHeung", "SSA_JangBogo" 같은 디렉토리 이름
    lat_deg: float          # 위도 [deg]
    lon_deg: float          # 경도 [deg]
    alt_m: float            # 고도 [m]
    cameras: Tuple[str, ...]  # 카메라 ID 리스트 (문자열)

    # ----------------------------------------------------------------------
    # 좌표 (process2 / process3 / analyze 들이 쓰는 다양한 튜플 형태)
    # ----------------------------------------------------------------------
    @property
    def location_lonlat_alt(self) -> Tuple[float, float, float]:
        """(lon, lat, alt) — process2_identification.get_site_and_cameras 호환."""
        return (self.lon_deg, self.lat_deg, self.alt_m)

    @property
    def location_latlon_alt(self) -> Tuple[float, float, float]:
        """(lat, lon, alt) — process3_iod / IOD 입력 호환."""
        return (self.lat_deg, self.lon_deg, self.alt_m)

    # ----------------------------------------------------------------------
    # 파일명 규칙 (사이트마다 명명이 다른 게 여러 파일에 흩어져 있어 여기로 모음)
    # ----------------------------------------------------------------------
    def obs_filename(self, date_str: str, cam: str) -> str:
        """raw 관측 파일명 (FTP 형식 포함)."""
        if self.name == "YoungYang_SSA":
            return f"{date_str}_{self.name}_data{cam}.txt"
        if self.name == "SSA_JangBogo":
            return f"AQ0{cam}_{date_str}_FTP.txt"
        return f"{date_str}_{self.name}_cam{cam}.txt"

    def mch_filename(self, date_str: str, cam: str) -> str:
        """MCH 식별 결과 파일명."""
        if self.name == "YoungYang_SSA":
            return f"{date_str}_{self.name}_data{cam}_MCH.txt"
        if self.name == "SSA_JangBogo":
            return f"AQ0{cam}_{date_str}_MCH.txt"
        return f"{date_str}_{self.name}_cam{cam}_MCH.txt"

    def resampled_filename(self, date_str: str, cam: str) -> str:
        """re_{date}_{site}_cam{cam}.txt (모든 사이트 동일 규칙)."""
        return f"re_{date_str}_{self.name}_cam{cam}.txt"

    def passed_all_filename(self, date_str: str, cam: str) -> str:
        """식별 결과 csv (모든 사이트 동일 규칙: cam{cam} 포함)."""
        return f"{date_str}_{self.name}_cam{cam}_passed_all.csv"


# =============================================================================
# 사이트 카탈로그 (여기만 수정하면 전체 파이프라인에 반영)
# =============================================================================
SITES = {
    "J":  Site("J",  "JangBoGo",      -74.6233, 164.2227,   50.0,
               tuple(str(i) for i in range(1, 8))),
    "G":  Site("G",  "GoHeung",        34.5261, 127.4470,   44.0,
               tuple(str(i) for i in range(1, 6))),
    "U":  Site("U",  "Ulsan",          35.6301, 129.2265,  160.0,
               tuple(str(i) for i in range(1, 6))),
    "H":  Site("H",  "HaeAn",          38.2552, 128.1136,  590.0,
               tuple(str(i) for i in range(1, 6))),
    "HC": Site("HC", "Hwacheon",       38.11833,127.433889,1027.0,
               tuple(str(i) for i in range(1, 8))),
    "UD": Site("UD", "Uniduck",        37.9921, 128.3223,  715.0,
               tuple(str(i) for i in range(1, 6))),
    "Y":  Site("Y",  "YoungYang",      36.8232, 129.2638,  365.0,
               tuple(str(i) for i in range(1, 8))),
    "YS": Site("YS", "YoungYang_SSA",  36.8232, 129.2638,  365.0,
               tuple(str(i) for i in range(101, 112))),
    "SJ": Site("SJ", "SSA_JangBogo",  -74.6233, 164.2227,   50.0,
               ("101", "102", "103", "105")),
}

# 보조 인덱스 (이름 → 코드)
_NAME_TO_CODE = {s.name: c for c, s in SITES.items()}


# =============================================================================
# 조회 API
# =============================================================================
def get_by_code(code: str) -> Site:
    """짧은 코드 ('G', 'SJ', ...)로 Site 반환. 대소문자 무관."""
    if not code:
        raise ValueError("site code is empty")
    key = code.strip().upper()
    if key not in SITES:
        raise ValueError(
            f"Unknown site code: {code!r}. Valid: {list(SITES.keys())}"
        )
    return SITES[key]


def get_by_name(name: str) -> Site:
    """디렉토리 이름 ('GoHeung', 'SSA_JangBogo', ...)으로 Site 반환."""
    if name not in _NAME_TO_CODE:
        raise ValueError(
            f"Unknown site name: {name!r}. Valid: {list(_NAME_TO_CODE.keys())}"
        )
    return SITES[_NAME_TO_CODE[name]]


def get_any(key: str) -> Site:
    """코드 또는 이름 무엇이든 받아서 Site 반환."""
    k = (key or "").strip()
    if k.upper() in SITES:
        return SITES[k.upper()]
    if k in _NAME_TO_CODE:
        return SITES[_NAME_TO_CODE[k]]
    raise ValueError(
        f"Unknown site: {key!r}. Codes: {list(SITES.keys())} "
        f"Names: {list(_NAME_TO_CODE.keys())}"
    )


def list_codes() -> List[str]:
    return list(SITES.keys())


def list_names() -> List[str]:
    return [s.name for s in SITES.values()]


# =============================================================================
# 대화형 입력 — 모든 process*.py 의 입력 단계를 이 한 함수로 통일
# =============================================================================
def prompt_site(prompt_label: str = "관측소 코드") -> Site:
    """사이트 코드 목록을 출력하고 코드를 입력받아 Site 반환."""
    print(f"{prompt_label}:")
    for code, s in SITES.items():
        print(f"  {code:>3s}  -  {s.name}")
    while True:
        raw = input("코드: ").strip().upper()
        try:
            return get_by_code(raw)
        except ValueError as e:
            print(f"[오류] {e}")


def prompt_cameras(site: Site,
                   allow_all: bool = True,
                   validate: bool = True) -> List[str]:
    """
    선택된 사이트의 카메라 목록을 보여주고 입력받아 list[str] 반환.

    입력 형식:
      - 'a' (또는 'A') : 전체 카메라
      - '1 2 3'        : 공백으로 구분된 ID 들
      - '101'          : 한 개
    """
    cam_str = ",".join(site.cameras)
    hint = "a=전체, " if allow_all else ""
    while True:
        raw = input(f"카메라 ({hint}가용 {cam_str}): ").strip()
        if not raw:
            print("[오류] 입력이 비었습니다.")
            continue
        if allow_all and raw.lower() == "a":
            return list(site.cameras)
        cams = raw.split()
        if validate:
            unknown = [c for c in cams if c not in site.cameras]
            if unknown:
                print(f"[경고] {site.name} 에 정의되지 않은 카메라: {unknown}. "
                      f"가용: {cam_str}")
                # 경고만 띄우고 그대로 사용
        return cams


def prompt_site_and_cameras(allow_all: bool = True,
                            validate: bool = True) -> Tuple[Site, List[str]]:
    """사이트 + 카메라 한 번에 입력. 모든 process*.py 진입점에서 호출."""
    site = prompt_site()
    cams = prompt_cameras(site, allow_all=allow_all, validate=validate)
    return site, cams


# =============================================================================
# 빠른 정합성 점검 — 모듈 로드 시 (개발용)
# =============================================================================
def _self_check():
    codes = list_codes()
    names = list_names()
    assert len(codes) == len(set(codes)), f"중복 코드: {codes}"
    assert len(names) == len(set(names)), f"중복 이름: {names}"


_self_check()


if __name__ == "__main__":
    # 모듈 단독 실행 시 카탈로그 표 출력
    print(f"{'code':>4s}  {'name':<16s}  {'lat':>9s}  {'lon':>10s}  "
          f"{'alt[m]':>7s}  cams")
    print("-" * 80)
    for code, s in SITES.items():
        print(f"{code:>4s}  {s.name:<16s}  {s.lat_deg:>9.4f}  {s.lon_deg:>10.4f}  "
              f"{s.alt_m:>7.1f}  {','.join(s.cameras)}")