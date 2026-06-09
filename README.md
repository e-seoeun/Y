# SSA Satellite Identification Pipeline

관측 streak (raw FTP) 을 받아 TLE/SGP4 기반으로 위성을 식별하는 파이프라인.

## 설계 원칙: CWD + 고정 파일명

각 프로그램은 **현재 작업 폴더(CWD)의 고정 이름 파일**만 읽고 쓴다.
경로·날짜·사이트·카메라 등 **어떤 외부 정보도 가정하지 않는다.** 처리할 자료가 든
폴더로 이동(`cd`)한 뒤 실행하면 된다. 기대하는 입력 파일이 없으면 오류를 낸다.

이렇게 경로/파일이름을 코드에 고정하지 않는 이유는, 실관측뿐 아니라
**simulated data, derived TLE 등 임의의 경우에도 동일한 프로그램을 그대로** 쓰기
위함이다. "무엇을 어디서 처리할지" 는 폴더 구성(준비 단계의 bash 등)이 담당한다.

```
./ftp.txt (또는 폴더 내 raw FTP 자동감지)  ──ftp2str.py──▶  ./str.txt
./site.txt ./str_p.txt ./catalog.txt        ──str2tle.py──▶  ./str_m.txt
```

- **`ftp2str.py`** : FTP file → simple streak. center 기준 ±1초 대칭 1초 grid 로 LS
  resampling, TLE matching 에 부적합한 noisy streak 은 제거. 입력 결정 순서:
  1. `ftp.txt` 가 있으면 → 그 파일만 처리 (고정 파일명)
  2. 없으면 → 현재 폴더의 `*.txt` 중 raw FTP 형식(`FF_*.fits` 블록 포함)을 **이름과
     무관하게 내용으로 자동 감지**하여 모두 처리, `str.txt` 하나로 통합. 한 폴더에
     카메라 여러 개(`*_cam1~5.txt`)가 있으면 자동으로 합쳐진다. (`re_*`, `SUMMARY`,
     `catalog.txt` 등 FTP 형식이 아닌 파일은 내용으로 자동 제외.)
- **`str2tle.py`** : TLE 후보군 SGP4 전파 → angular distance / PA / 각속도 / D1·D2 기반
  stage-1 / stage-2 평가 → 최적 매칭 1개.

## 입출력 파일

| 프로그램 | 입력 (CWD) | 출력 (CWD) |
|---|---|---|
| `ftp2str.py` | `ftp.txt` (없으면 raw FTP `*.txt` 자동감지) | `str.txt` |
| `str2tle.py` | `site.txt`, `str_p.txt`, `catalog.txt` | `str_m.txt` |

- **`ftp.txt`** : raw FTP 관측 파일. 이 이름이 있으면 그것만 처리한다. 없으면 폴더 내
  raw FTP `*.txt`(예: `*_cam1~5.txt`)를 내용으로 감지해 모두 통합 처리한다.
- **`str.txt`** : streak 당 `line1`(property) + `line2~N`(1초 간격 자료) + 구분선.
  - `line1` : `streak_id  N  time  RA_center  Dec_center  MotionAngle  Speed`
  - `line2~N` : `time  RA  Dec  MeanIntensity`  (N = 1초 간격 자료 개수)
  - `streak_id` : FTP image name + streak number 를 합친 단일 문자열.
- **`str_p.txt`** : 각 streak 의 `line1` 만 모은 파일 (TLE matching 입력).
  여러 카메라 `str.txt` 를 합친 뒤 `grep` 으로 추출:
  ```bash
  cat cam*/str.txt > str.txt        # 카메라 통합
  grep '^FF' str.txt > str_p.txt    # property line(line1)만 추출
  ```
  *(참고: `str2tle.py` 는 body 라인을 무시하므로 `str.txt` 를 그대로 입력해도 동작한다.)*
- **`site.txt`** : 관측소 위치. 처음 등장하는 숫자 3개를 `lat, long, elevation(m)`
  순으로 읽는다. `#` 주석과 `lat = 37.5` 같은 라벨 형식 허용.
  ```
  lat 34.5261
  lon 127.4470
  elev 44.0
  ```
- **`catalog.txt`** : TLE. published / classified / 관심 TLE 1개 무엇이든 가능
  (객체당 3줄: name + line1 + line2).
- **`str_m.txt`** : `str_p.txt` 각 line 에 매칭 결과를 덧붙인 결과 (아래 포맷 참조).

## 설치

```bash
pip install -r requirements.txt
```

Python 3.9+ 권장. 의존성: `numpy`, `pandas`, `astropy`, `sgp4`

## 사용법

자료 폴더로 이동한 뒤 실행한다. 코드는 어디에 두어도 무방하다(예: `C:\tools\SSA`).

```bash
cd /data/case01     # ftp.txt 등이 있는 폴더

# 단계별 (각 스크립트 단독 실행)
python /path/to/ftp2str.py
python /path/to/str2tle.py

# 또는 main.py 로
python /path/to/main.py ftp2str
python /path/to/main.py str2tle
python /path/to/main.py run        # ftp2str + str2tle (str.txt 를 바로 매칭)
python /path/to/main.py            # 인자 없이 대화형
```

## 결과 파일 포맷 (`str_m.txt`)

```
# streak_name   N  time                      RA_center  Dec_center  MotionAng     Speed  NORAD   d1   d2   da   dv
FF_KR0004_..._0001  5  2025-05-17T11:14:00.376  111.5258  71.7837  52.2599  0.628533  57435  ...
...
FF_KR0004_..._0002  9  2025-05-17T11:28:18.832  216.0493  85.3717 135.8007  0.812431  no_match  -  -  -  -
```

컬럼:
- `NORAD` : 매칭된 위성번호 또는 `no_match`
- `d1`, `d2` : observation vs candidate 선분의 normal / parallel 거리 (deg)
- `da` : position angle 차이 (deg)
- `dv` : 각속도 상대오차 (|cand - obs| / |obs|)

매칭 실패 시 `d1~dv` 는 `-`.

> `str_p.txt` 에는 1초 grid body 가 없으므로, `d1/d2` 계산에 필요한 center ±1초 두 점은
> header 의 `(RA_center, Dec_center, MotionAngle, Speed)` 로부터 great-circle 상에서
> 해석적으로 복원한다 (`ftp2str` 의 motion 계산 역연산).

## 보조 모듈

```
SSA/
├── main.py              # CLI 진입점 (CWD 고정 파일명)
├── ftp2str.py           # FTP -> str.txt
├── str2tle.py           # site/str_p/catalog -> str_m.txt
├── candidate_search.py  # TLE/SGP4 후보 탐색
├── motion.py            # angular motion (관측 / 후보)
└── evaluate.py          # D1/D2 + stage1/stage2 평가
```