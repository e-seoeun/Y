# SSA Satellite Identification Pipeline

관측 streak (raw FTP) 을 받아 TLE/SGP4 기반으로 위성을 식별하는 파이프라인.

두 개의 프로그램으로 이루어진다.

```
raw FTP (관측)                         ──ftp2str──▶   str.txt   (1초 간격 streak)
str.txt + site.txt + catalog(TLE)      ──str2tle──▶   str_m.txt (식별 결과)
```

- **`ftp2str`** : FTP 관측 파일에서 streak 속성을 뽑아 1초 간격으로 resampling 하고,
  TLE 매칭에 부적합한 noisy streak 을 걸러 `str.txt` 를 만든다.
- **`str2tle`** : `str.txt` 의 각 streak 에 대해 TLE 후보를 SGP4 로 전파하여 가장
  잘 맞는 위성 1개를 찾아 `str_m.txt` 를 만든다.

---

## 설계 원칙: "현재 폴더 + 고정 파일명"

각 프로그램은 **현재 작업 폴더(CWD)의 고정 이름 파일**만 읽고 쓴다. 경로·날짜·사이트·
카메라 등 **어떤 외부 정보도 가정하지 않는다.** 처리할 자료가 든 폴더로 이동(`cd`)한
뒤 명령을 실행하면 된다. 기대하는 입력이 없으면 오류를 낸다.

이렇게 한 이유는 실관측뿐 아니라 **simulated data, derived TLE 등 임의의 경우에도
같은 프로그램을 그대로** 쓰기 위함이다. "무엇을 어디서 처리할지"는 폴더 구성이 담당한다.

| 프로그램 | 입력 (현재 폴더) | 출력 (현재 폴더) |
|---|---|---|
| `ftp2str` | `ftp.txt` *(없으면 raw FTP `*.txt` 자동 감지)* | `str.txt` |
| `str2tle` | `site.txt`, `str_p.txt`·`str.txt`, `catalog.txt`·`*catalog.txt` | `str_m.txt` |

---

## 설치

Python 3.9 이상. 의존성: `numpy`, `pandas`, `astropy`, `sgp4`.

### 1) 코드 받기

```bash
git clone https://github.com/e-seoeun/Y.git SSA
cd SSA
```

### 2) 환경 만들고 설치 — **sudo 권한 유무에 따라**

**(A) `python3-venv` 가 이미 깔려 있다면 (권장)**
```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e .
```

**(B) sudo 가 없고 venv 생성이 안 될 때 — 사용자 영역(`--user`) 설치**
```bash
pip install --user -e .
# 명령(ftp2str 등)이 ~/.local/bin 에 생긴다. PATH 에 없으면 한 번 추가:
export PATH="$HOME/.local/bin:$PATH"          # 영구 적용은 ~/.bashrc 에 추가
```

**(C) conda 를 쓴다면 (sudo 불필요)**
```bash
conda create -n ssa python=3.11 -y
conda activate ssa
pip install -e .
```

설치가 되면 어느 폴더에서든 다음 명령을 쓸 수 있다:

| 명령 | 동작 |
|---|---|
| `ftp2str` | 현재 폴더 raw FTP → `str.txt` |
| `str2tle` | 현재 폴더 입력 → `str_m.txt` |
| `ssa run` | `ftp2str` + `str2tle` 연속 실행 |

> **설치 없이 쓰기:** `pip install -r requirements.txt` 로 의존성만 깔고,
> 명령 대신 `python3 /경로/SSA/ftp2str.py` 처럼 스크립트를 직접 실행해도 된다.

### 설치 확인
```bash
which ftp2str        # 경로가 나오면 성공
```

---

## 입력 파일 준비

처리할 자료 폴더에 아래 파일들을 둔다. 이름은 고정이지만 **자동 감지** 규칙이 있어
유연하다.

### `ftp.txt` (ftp2str 입력)
raw FTP 관측 파일. 다음 순서로 입력이 결정된다.
1. `ftp.txt` 가 있으면 → 그 파일만 처리.
2. 없으면 → 폴더 안 `*.txt` 중 **raw FTP 형식(`FF_*.fits` 블록 포함)** 을 이름과
   무관하게 내용으로 자동 감지하여 **모두 통합**해 `str.txt` 하나로 만든다.
   - `*_cam1.txt ~ *_cam7.txt`, `AQ0101_..._FTP.txt` 등 **이름이 달라도** 잡힌다.
   - `re_*`, `*SUMMARY*`, 이미지/압축/`.cal` 등 FTP 가 아닌 파일은 자동 제외.

> 여러 카메라가 한 폴더에 있으면 자동으로 합쳐진다. 굳이 `ftp.txt` 로 따로 만들 필요
> 없이 폴더에서 `ftp2str` 만 실행하면 된다.

### 관측소 위치 (str2tle 입력)
다음 순서로 결정된다.
1. `site.txt` 가 있으면 → 그것을 사용.
2. 없으면 → 폴더 안 **`*_SUMMARY.txt`** 에서 `lat`, `lon`, `elev` 를 자동으로 읽는다
   (`"elev": 44.0  "lat": 34.526  "lon": 127.447` 형식). **대부분 별도 준비가 필요 없다.**

`site.txt` 를 직접 만들 경우 숫자 3개를 **`lat, lon, elevation(m)` 순서**로 적는다
(공백/콤마 구분, `#` 주석, `lat=..` 라벨 형식 허용):
```
34.5261, 127.4470, 44.0
```
> 라벨(`lat`/`lon`/`elev`)이 있으면 순서와 무관하게 읽고, 없으면 위 순서로 해석한다.

### `catalog.txt` (str2tle 입력) — TLE
published / classified / 관심 위성 1개 무엇이든 가능. 객체당 3줄(name + line1 + line2).
- `catalog.txt` 가 있으면 그것을 쓰고, 없으면 `*catalog.txt` 로 끝나는 파일
  (예: `20250517_0002_catalog.txt`) 을 자동으로 찾는다.

### `str_p.txt` (선택) — str2tle 입력
str2tle 는 streak 의 첫 줄(header)만 읽으므로 `str.txt` 를 그대로 입력해도 된다.
`str_p.txt` 가 있으면 그것을, 없으면 `str.txt` 를 자동으로 쓴다.

> 명세상 여러 카메라 결과를 합쳐 `grep` 으로 header 만 뽑아 `str_p.txt` 를 만들 수
> 있지만, 위 자동 통합/자동 선택 때문에 보통 **필요 없다.**
> ```bash
> grep '^FF' str.txt > str_p.txt    # 필요할 때만
> ```

---

## 사용법

자료 폴더로 이동해서 명령만 실행한다.

```bash
cd /path/to/데이터폴더

ftp2str        # raw FTP → str.txt
str2tle        # str.txt + site.txt + catalog → str_m.txt
# 또는 한 번에
ssa run
```

설치 없이 스크립트로:
```bash
python3 /path/to/SSA/ftp2str.py
python3 /path/to/SSA/str2tle.py
python3 /path/to/SSA/main.py run
```

### 여러 폴더 일괄 실행 (`scripts/run_all.sh`)

자료가 `YSPACE/YYYYMMDD/site_name/` 구조이고 TLE 가 `YSPACE/TLE/YYYYMMDD_HHMM_catalog.txt`
형태로 모여 있는 환경을 위한 보조 스크립트.

- **입력은 `YSPACE` 에서 읽기만** 하고(원본이 읽기 전용이어도 됨), **결과는 쓰기 가능한
  출력 폴더(`OUTBASE`)** 아래 `YYYYMMDD/site_name/` 에 만든다.
- 각 폴더마다: 그날 **가장 이른 시간의 catalog** 를 `catalog.txt` 로 링크 →
  `ftp2str` → `str2tle`. 관측소 좌표는 `*_SUMMARY.txt` 에서 자동으로 읽는다.

```bash
chmod +x ~/SSA/scripts/run_all.sh
~/SSA/scripts/run_all.sh                       # 기본: YSPACE=~/YSPACE, OUTBASE=~/ssa_out
~/SSA/scripts/run_all.sh /path/to/YSPACE /path/to/out   # 경로 직접 지정
```
결과: `OUTBASE/<날짜>/<사이트>/str_m.txt`

> 입력 폴더가 읽기 전용(예: 공유 스토리지)이라 그 자리에 결과를 쓸 수 없을 때를 위한
> 구성이다. 이 스크립트는 특정 구조(YSPACE)·TLE 이름 규칙에 맞춘 예시이며, 다른 구조라면
> 상단 주석을 참고해 수정한다. 핵심 프로그램(`ftp2str`/`str2tle`)은 구조와 무관하게
> "현재 폴더 + 고정 파일명" 으로만 동작한다.

---

## 결과 파일 포맷 (`str_m.txt`)

```
# streak_name   N  time                      RA_center  Dec_center  MotionAng     Speed  NORAD   d1   d2   da   dv
FF_KR0004_..._0001  5  2025-05-17T11:14:00.376  111.5258  71.7837   52.2599  0.628533  57435  ...
...
FF_KR0004_..._0002  9  2025-05-17T11:28:18.832  216.0493  85.3717  135.8007  0.812431  no_match  -  -  -  -
```

| 컬럼 | 의미 |
|---|---|
| `streak_name` | FTP image name + streak number |
| `N` | 1초 간격 자료 개수 |
| `time` | center 시각 (UTC) |
| `RA_center` / `Dec_center` | center 의 적경/적위 (deg) |
| `MotionAng` / `Speed` | streak 의 position angle (deg) / 각속도 (deg/s) |
| `NORAD` | 매칭된 위성번호, 없으면 `no_match` |
| `d1` / `d2` | obs vs candidate 선분의 normal / parallel 거리 (deg) |
| `da` | position angle 차이 (deg) |
| `dv` | 각속도 상대오차 `|cand-obs|/|obs|` |

매칭 실패 시 `d1~dv` 는 `-`.

---

## 자주 묻는 문제

- **`입력 FTP 파일을 ... 찾지 못했습니다`** : 현재 폴더에 raw FTP(`FF_*.fits` 블록을
  가진 `*.txt`)가 없다. 폴더 위치를 확인하거나 `ftp.txt` 를 둔다.
- **`streak 파일이 ... 없습니다 (str_p.txt 또는 str.txt)`** : `ftp2str` 를 먼저 실행해
  `str.txt` 를 만든 뒤 `str2tle` 를 실행한다.
- **`catalog 파일이 ... 없습니다`** : 폴더에 `catalog.txt` 또는 `*catalog.txt` 가 없다.
- **매칭 결과가 이상함** : `site.txt` 의 순서가 `lat, lon, elev` 인지 확인한다.
- **명령(`ftp2str`)을 못 찾음** : venv/conda 를 `activate` 했는지, 또는 `--user` 설치 시
  `~/.local/bin` 이 PATH 에 있는지 확인한다.

---

## 진행 표시
`str2tle` 는 실행 중 한 줄짜리 진행 바로 `진행률 / matched 수 / ETA` 를 보여준다.
```
  [███████████████░░░░░░░░░░░░░░░]  50.0%  6894/13788  matched=5800  ETA 41s
```

## 모듈 구성
```
ftp2str.py            # FTP -> str.txt
str2tle.py            # site/str/catalog -> str_m.txt
main.py               # CLI (ftp2str / str2tle / run)
candidate_search.py   # TLE/SGP4 후보 탐색
motion.py             # angular motion (관측 / 후보)
evaluate.py           # D1/D2 + stage1/stage2 평가
```
