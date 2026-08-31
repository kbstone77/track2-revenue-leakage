# -*- coding: utf-8 -*-
"""
Track 2 Mission — 데이터 정리 & 매출 누수 진단 파이프라인
표준 라이브러리만 사용 (pandas 불필요).

입력 : data/raw/*.csv
출력 : data/clean/*.csv          정규화된 테이블 (Supabase 적재용)
       public/data/dataset.json  대시보드 구동 데이터
       public/data/cleaning_log.json  처리 로그 (규칙 단위 · 레코드 단위 카운트)
       pipeline/seed.sql         Supabase INSERT 시드
실행 : python pipeline/clean.py
"""

import csv, json, os, re, sys, collections, datetime, io, math, statistics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
CLEAN = os.path.join(ROOT, "data", "clean")
OUT = os.path.join(ROOT, "public", "data")
os.makedirs(CLEAN, exist_ok=True)
os.makedirs(OUT, exist_ok=True)

# ─────────────────────────────────────────────────────────────
# 처리 로그
# ─────────────────────────────────────────────────────────────
LOG = []          # 규칙 단위 로그
SAMPLES = collections.defaultdict(list)   # 규칙별 before/after 예시


def log(table, field, rule, action, count, note="", severity="normal"):
    LOG.append({
        "table": table, "field": field, "rule": rule, "action": action,
        "count": count, "note": note, "severity": severity,
        "samples": SAMPLES.get(rule, [])[:4],
    })


def sample(rule, before, after):
    if len(SAMPLES[rule]) < 4:
        SAMPLES[rule].append({"before": before, "after": after})


def load(name):
    with open(os.path.join(RAW, name), encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# ─────────────────────────────────────────────────────────────
# 공통 정규화 헬퍼
# ─────────────────────────────────────────────────────────────
def norm_date(s):
    """YYYY-MM-DD / MM-DD-YYYY / YYYY/MM/DD / YYYYMMDD → date | None"""
    s = (s or "").strip()
    if not s:
        return None
    pats = [
        (r"^(\d{4})-(\d{2})-(\d{2})$", (1, 2, 3)),
        (r"^(\d{4})/(\d{2})/(\d{2})$", (1, 2, 3)),
        (r"^(\d{2})-(\d{2})-(\d{4})$", (3, 1, 2)),   # MM-DD-YYYY
        (r"^(\d{4})(\d{2})(\d{2})$", (1, 2, 3)),
    ]
    for pat, (y, m, d) in pats:
        mt = re.match(pat, s)
        if mt:
            try:
                return datetime.date(int(mt.group(y)), int(mt.group(m)), int(mt.group(d)))
            except ValueError:
                return None
    return None


def date_kind(s):
    s = (s or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s): return "ISO(YYYY-MM-DD)"
    if re.match(r"^\d{4}/\d{2}/\d{2}$", s): return "슬래시(YYYY/MM/DD)"
    if re.match(r"^\d{2}-\d{2}-\d{4}$", s): return "미국식(MM-DD-YYYY)"
    if re.match(r"^\d{8}$", s): return "구분자없음(YYYYMMDD)"
    return "미상"


def norm_phone(s):
    d = re.sub(r"\D", "", s or "")
    if len(d) == 11 and d.startswith("010"):
        return f"{d[:3]}-{d[3:7]}-{d[7:]}"
    if len(d) == 10:
        return f"{d[:3]}-{d[3:6]}-{d[6:]}"
    return None


def to_int(s):
    s = (s or "").strip().replace(",", "")
    if s in ("", "-", "NA", "N/A", "null", "None"):
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def sq(v):
    """SQL 리터럴"""
    if v is None or v == "":
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


# ─────────────────────────────────────────────────────────────
# 1. UNITS (기준 테이블)
# ─────────────────────────────────────────────────────────────
raw_units = load("units.csv")
units = []
for r in raw_units:
    units.append({
        "unit_id": r["unit_id"].strip(),
        "building": r["building"].strip(),
        "floor": to_int(r["floor"]),
        "unit_no": r["unit_no"].strip(),
        "type": r["type"].strip(),
        "area_m2": to_int(r["area_m2"]),
        "price": to_int(r["price_10k_krw"]),
        "status": r["status"].strip(),
        "direction": r["direction"].strip(),
        "view": r["view"].strip(),
    })
UNIT = {u["unit_id"]: u for u in units}
log("units", "-", "units_passthrough", "기준 테이블 — 공백만 제거 후 그대로 사용",
    len(units), "다른 표의 unit_id 유효성 검증 기준으로 사용")

# 타입별 마스터 표시가 (스케일 검증은 계약 데이터 적재 후 수행 → §5.5)
type_price = collections.defaultdict(list)
for u in units:
    if u["price"]:
        u["price_listed"] = u["price"]
        type_price[u["type"]].append(u["price"])

# ─────────────────────────────────────────────────────────────
# 2. CUSTOMERS
# ─────────────────────────────────────────────────────────────
raw_cust = load("customers_raw.csv")

GRADE_MAP = {"vip": "VIP", "VIP": "VIP", "Vip": "VIP", "VVIP": "VVIP",
             "일반": "일반", "신규": "신규", "관심고객": "관심고객"}
REGION_MAP = {"부산시": "부산", "Busan": "부산", "경기도": "경기", "Gyeonggi": "경기",
              "서울시": "서울", "seoul": "서울", "Seoul": "서울"}
# 유입경로 → 광고비 테이블(ad_spend)의 채널명으로 통일
SOURCE_MAP = {
    "블로그": ("블로그", "확정"), "Blog": ("블로그", "확정"),
    "SNS": ("SNS광고", "확정"),
    "광고(네이버)": ("포털검색광고", "확정"),
    "온라인전시관": ("전시관행사", "확정"),
    "지인소개": ("지인추천이벤트", "확정"),
    "광고": ("옥외광고", "추정"),          # 매체 미표기 — 남은 유료채널로 추정 매핑
    "방문": ("직접유입", "확정"),
    "전화문의": ("직접유입", "확정"),
}

cust_rows, seen_id = [], {}
cnt = collections.Counter()
for r in raw_cust:
    cid = r["customer_id"].strip()
    name = r["name"].strip()
    if r["name"] != name:
        cnt["ws_name"] += 1

    g_raw = r["grade"]
    grade = GRADE_MAP.get(g_raw.strip(), g_raw.strip())
    if g_raw != grade:
        cnt["grade"] += 1
        sample("grade_norm", repr(g_raw), grade)

    reg_raw = r["region"].strip()
    region = REGION_MAP.get(reg_raw, reg_raw)
    if region != reg_raw:
        cnt["region"] += 1
        sample("region_norm", reg_raw, region)

    src_raw = r["lead_source"].strip()
    src, conf = SOURCE_MAP.get(src_raw, (src_raw, "확정"))
    if src != src_raw:
        cnt["source"] += 1
        sample("source_map", src_raw, f"{src} ({conf})")

    ph_raw = r["phone"].strip()
    phone = norm_phone(ph_raw)
    if ph_raw and phone and ph_raw != phone:
        cnt["phone"] += 1
        sample("phone_norm", ph_raw, phone)
    if not ph_raw:
        cnt["phone_missing"] += 1

    email = r["email"].strip()
    email_valid = bool(email) and "@" in email and email.isascii()
    if email and not email.isascii():
        cnt["email_nonascii"] += 1
        sample("email_flag", email, "→ email_valid=false (비ASCII 로컬파트)")
    if not email:
        cnt["email_missing"] += 1

    b_raw = (r["budget_10k_krw"] or "").strip()
    budget = to_int(b_raw)
    if budget is not None and (budget <= 0 or budget >= 999999):
        cnt["budget_sentinel"] += 1
        sample("budget_sentinel", b_raw, "NULL (미상값 sentinel)")
        budget = None

    sd_raw = r["signup_date"]
    sd = norm_date(sd_raw)
    if sd and date_kind(sd_raw) != "ISO(YYYY-MM-DD)":
        cnt["date_signup"] += 1
        sample("date_signup", f"{sd_raw} [{date_kind(sd_raw)}]", sd.isoformat())

    row = {
        "customer_id": cid, "name": name, "phone": phone,
        "email": email or None, "email_valid": email_valid,
        "grade": grade, "lead_source": src, "lead_source_conf": conf,
        "interest_type": r["interest_type"].strip(),
        "signup_date": sd.isoformat() if sd else None,
        "region": region, "budget": budget, "status": r["status"].strip(),
    }
    # 완전성 점수가 높은 레코드를 대표로 채택
    score = sum([bool(phone) * 2, bool(email_valid), budget is not None, bool(sd)])
    if cid in seen_id:
        cnt["dup_id"] += 1
        sample("dup_id", f"{cid} / {ph_raw or '(전화없음)'}", "완전성 높은 1건만 채택")
        if score > seen_id[cid][0]:
            seen_id[cid] = (score, row)
    else:
        seen_id[cid] = (score, row)

customers = [v[1] for v in seen_id.values()]

# 동일인 추정 (이름+전화 동일하나 ID가 다른 경우)
byperson = collections.defaultdict(list)
for c in customers:
    if c["phone"]:
        byperson[(c["name"], c["phone"])].append(c["customer_id"])
cross_dup = {k: v for k, v in byperson.items() if len(v) > 1}
for c in customers:
    c["dup_person_of"] = None
for k, ids in cross_dup.items():
    keep = sorted(ids)[0]
    for i in ids[1:]:
        for c in customers:
            if c["customer_id"] == i:
                c["dup_person_of"] = keep
    sample("cross_dup", f"{k[0]} {k[1]} → {', '.join(ids)}", f"대표 ID {keep}로 연결(삭제 안 함)")

CUST = {c["customer_id"]: c for c in customers}

log("customers", "customer_id", "dup_id", "동일 customer_id 중복행 → 완전성 점수 높은 1건만 채택",
    cnt["dup_id"], "전화 포맷·공백만 다른 완전 중복. 648행 → %d행" % len(customers), "high")
log("customers", "name/phone", "cross_dup", "이름+전화 동일 · ID 다른 건 → 동일인 후보로 태깅",
    sum(len(v) - 1 for v in cross_dup.values()), "삭제하지 않고 dup_person_of로 연결 (오삭제 방지)", "high")
log("customers", "grade", "grade_norm", "등급 표기 통일 (vip/Vip/VIP, 앞뒤 공백)",
    cnt["grade"], "VVIP·VIP·일반·신규·관심고객 5단계로 수렴")
log("customers", "region", "region_norm", "지역 표기 통일 (부산시/Busan→부산 등)",
    cnt["region"], "24종 → 17개 시도로 수렴")
log("customers", "lead_source", "source_map", "유입경로 → 광고비 테이블 채널명으로 매핑",
    cnt["source"], "'광고'는 매체 미표기 → 옥외광고로 추정 매핑(conf=추정), ROI 해석 시 주의", "high")
log("customers", "phone", "phone_norm", "전화번호 010-XXXX-XXXX 형식으로 통일",
    cnt["phone"], "01012345678 / 010.1234.5678 3종 포맷 혼재")
log("customers", "phone", "phone_missing", "전화번호 결측 → NULL 유지",
    cnt["phone_missing"], "임의 값 대체 금지. 연락 불가 고객으로 별도 집계")
log("customers", "email", "email_flag", "한글 로컬파트 이메일 → email_valid=false 플래그",
    cnt["email_nonascii"], "값은 보존하되 발송 대상에서 제외 (삭제 시 원인 추적 불가)", "high")
log("customers", "email", "email_missing", "이메일 결측 → NULL 유지", cnt["email_missing"])
log("customers", "budget_10k_krw", "budget_sentinel", "예산 -1 / 999999 → NULL (미상 sentinel)",
    cnt["budget_sentinel"], "평균 예산 계산에서 제외. 미기재 %d건은 예산 적합도 판정 불가로 처리" % cnt["budget_sentinel"], "high")
log("customers", "signup_date", "date_signup", "가입일 4종 포맷 → ISO(YYYY-MM-DD) 통일", cnt["date_signup"])

# ─────────────────────────────────────────────────────────────
# 3. CONSULTATIONS
# ─────────────────────────────────────────────────────────────
raw_cons = load("consultations_raw.csv")
CHANNEL_MAP = {"phone": "전화", "Phone": "전화", "전화": "전화",
               "방문상담": "방문", "방문": "방문", "SNS": "SNS", "온라인": "온라인"}
cons, c2 = [], collections.Counter()
datefmt_seen = collections.Counter()
for r in raw_cons:
    ch_raw = r["channel"]
    ch = CHANNEL_MAP.get(ch_raw.strip(), ch_raw.strip())
    if ch != ch_raw:
        c2["channel"] += 1
        sample("cons_channel", repr(ch_raw), ch)

    oc_raw = r["outcome"]
    oc = oc_raw.strip()
    if oc != oc_raw:
        c2["outcome_ws"] += 1
        sample("cons_outcome", repr(oc_raw), oc)

    d_raw = r["consult_date"]
    kind = date_kind(d_raw)
    datefmt_seen[kind] += 1
    d = norm_date(d_raw)
    if kind != "ISO(YYYY-MM-DD)":
        c2["date"] += 1
        sample("cons_date", f"{d_raw} [{kind}]", d.isoformat() if d else "NULL")

    dur = to_int(r["duration_min"])
    dur_flag = None
    if dur is not None and dur <= 0:
        c2["dur_nonpos"] += 1
        sample("dur_nonpos", str(dur), "NULL (측정오류)")
        dur_flag, dur = "비정상(0이하)", None
    elif dur is not None and dur > 120:
        c2["dur_high"] += 1
        sample("dur_high", str(dur), "값 보존 + outlier 플래그, 평균 산출 시 120분으로 winsorize")
        dur_flag = "이상치(장시간)"

    cid, uid = r["customer_id"].strip(), r["unit_id"].strip()
    orphan = []
    if cid not in CUST:
        orphan.append("customer")
        c2["orphan_cust"] += 1
        sample("orphan_cust", f"{r['consult_id']} → {cid}", "고객 마스터에 없음 → 추적 불가 리드로 격리")
    if uid not in UNIT:
        orphan.append("unit")
        c2["orphan_unit"] += 1
        sample("orphan_unit", f"{r['consult_id']} → {uid}", "매물 마스터에 없음 → 매물 집계에서 제외")

    cons.append({
        "consult_id": r["consult_id"].strip(),
        "customer_id": cid if cid in CUST else None,
        "customer_id_raw": cid,
        "unit_id": uid if uid in UNIT else None,
        "unit_id_raw": uid,
        "consultant": r["consultant"].strip(),
        "channel": ch,
        "consult_date": d.isoformat() if d else None,
        "duration_min": dur,
        "duration_flag": dur_flag,
        "outcome": oc,
        "notes": r["notes"].strip(),
        "orphan": ",".join(orphan) or None,
    })

log("consultations", "consult_date", "cons_date", "날짜 4종 포맷 → ISO 통일",
    c2["date"], " · ".join(f"{k} {v}건" for k, v in datefmt_seen.most_common()), "high")
log("consultations", "channel", "cons_channel", "채널 표기 통일 (phone/Phone/전화 · 방문상담/방문)",
    c2["channel"], "8종 → 4종(전화·방문·SNS·온라인)")
log("consultations", "outcome", "cons_outcome", "상담결과 앞뒤 공백 제거", c2["outcome_ws"],
    "'계약희망 '과 '계약희망'이 다른 값으로 집계되던 문제")
log("consultations", "duration_min", "dur_nonpos", "상담시간 0분 이하 → NULL",
    c2["dur_nonpos"], "음수(-5)는 물리적으로 불가 → 측정오류로 판정", "high")
log("consultations", "duration_min", "dur_high", "상담시간 120분 초과 → 값 보존 + 이상치 플래그",
    c2["dur_high"], "삭제하지 않음. 평균 산출 시에만 120분으로 winsorize")
log("consultations", "customer_id", "orphan_cust", "고객 마스터에 없는 customer_id → NULL 처리 후 격리",
    c2["orphan_cust"], "행을 지우지 않음 — '추적 불가 리드'로 누수 금액 산정에 사용", "high")
log("consultations", "unit_id", "orphan_unit", "매물 마스터에 없는 unit_id → NULL 처리 후 격리",
    c2["orphan_unit"], "매물별 집계에서만 제외, 상담 건수에는 포함", "high")

# ─────────────────────────────────────────────────────────────
# 4. CONTRACTS
# ─────────────────────────────────────────────────────────────
raw_ctr = load("contracts_raw.csv")
STATUS_MAP = {"완료": "완료", "complete": "완료", "Completed": "완료",
              "진행중": "진행중", "해지": "해지"}
ctrs, c3 = [], collections.Counter()
ctr_datefmt = collections.Counter()
for r in raw_ctr:
    st_raw = r["status"]
    st = STATUS_MAP.get(st_raw.strip(), st_raw.strip())
    if st != st_raw:
        c3["status"] += 1
        sample("ctr_status", repr(st_raw), st)

    d_raw = r["contract_date"]
    k = date_kind(d_raw)
    ctr_datefmt[k] += 1
    d = norm_date(d_raw)
    if k != "ISO(YYYY-MM-DD)":
        c3["date"] += 1
        sample("ctr_date", f"{d_raw} [{k}]", d.isoformat() if d else "NULL")

    price = to_int(r["price_10k_krw"])
    price_flag = None
    if price is not None and price <= 0:
        c3["price0"] += 1
        sample("ctr_price0", f"{r['contract_id']} 분양가 0", "동일 타입 실계약가 중앙값으로 대체 (§5.5)")
        price, price_flag = None, "동일타입 실계약가 중앙값으로 대체"

    loan = to_int(r["loan_amount_10k_krw"])
    loan_flag = None
    if loan is None:
        c3["loan_missing"] += 1
        loan_flag = "결측"
        sample("loan_missing", f"{r['contract_id']} 대출금 공란", "NULL 유지 (0원 대체 금지)")

    down = to_int(r["down_payment_10k_krw"])
    ctrs.append({
        "contract_id": r["contract_id"].strip(),
        "customer_id": r["customer_id"].strip(),
        "unit_id": r["unit_id"].strip(),
        "contract_date": d.isoformat() if d else None,
        "price": price, "price_flag": price_flag,
        "down_payment": down,
        "loan_amount": loan, "loan_flag": loan_flag,
        "status": st,
        "sales_rep": r["sales_rep"].strip(),
    })

# 동일 매물 중복 계약 검증
by_unit = collections.defaultdict(list)
for c in ctrs:
    by_unit[c["unit_id"]].append(c)
multi_live = 0
for uid, lst in by_unit.items():
    live = [c for c in lst if c["status"] in ("완료", "진행중")]
    if len(live) > 1:
        multi_live += len(live) - 1
        sample("unit_conflict", f"{uid}: {', '.join(c['contract_id']+'('+c['status']+')' for c in live)}",
               "동일 매물 유효계약 2건 이상 → 검증 대상")

log("contracts", "status", "ctr_status", "계약상태 표기 통일 (완료/complete/Completed)",
    c3["status"], "6종 → 3종(완료·진행중·해지)", "high")
log("contracts", "contract_date", "ctr_date", "계약일 4종 포맷 → ISO 통일", c3["date"],
    " · ".join(f"{k} {v}건" for k, v in ctr_datefmt.most_common()))
log("contracts", "price_10k_krw", "ctr_price0", "분양가 0원 → 동일 타입 실계약가 중앙값으로 대체",
    c3["price0"], "0원 계약은 존재할 수 없음. 마스터 표시가는 스케일이 달라 사용 불가(§5.5) → 대체 사실을 price_flag에 기록", "high")
log("contracts", "loan_amount_10k_krw", "loan_missing", "대출금액 결측 → NULL 유지",
    c3["loan_missing"], "0원으로 채우면 '현금 완납'과 구분 불가 → 평균 대출액에서 제외", "high")
log("contracts", "unit_id", "unit_conflict", "동일 매물에 유효계약 2건 이상 → 충돌 플래그",
    multi_live, "해지 후 재계약이 아닌 진짜 충돌만 카운트", "high" if multi_live else "normal")

# ─────────────────────────────────────────────────────────────
# 5. AD SPEND
# ─────────────────────────────────────────────────────────────
raw_ad = load("ad_spend.csv")
ads = [{"month": r["month"].strip(), "channel": r["channel"].strip(),
        "spend": to_int(r["spend_10k_krw"]), "impressions": to_int(r["impressions"]),
        "clicks": to_int(r["clicks"]), "leads": to_int(r["leads"])} for r in raw_ad]
log("ad_spend", "-", "ad_passthrough", "정돈된 상태 — 타입 변환만 수행", len(ads),
    "2024-01 ~ 2025-12 (24개월 × 6채널)")

# 기간 정합성: 광고비는 2025-12까지인데 상담/계약은 2026년까지 존재
cons_max = max((c["consult_date"] for c in cons if c["consult_date"]), default="")
ad_max = max(a["month"] for a in ads)
period_gap = cons_max[:7] > ad_max
if period_gap:
    log("cross", "기간", "period_gap", "광고비 기간(~%s)과 상담 기간(~%s) 불일치 → ROI는 겹치는 구간만 산출" % (ad_max, cons_max[:7]),
        1, "2026년 상담·계약은 채널 ROI 계산에서 제외해야 왜곡이 없음", "high")

# ─────────────────────────────────────────────────────────────
# 5.5 교차 검증 — 가격 스케일 정합성 (핵심 발견)
# ─────────────────────────────────────────────────────────────
# units.price(마스터 표시가)와 contracts.price(실계약가)는 서로 다른 스케일이며
# 상관관계도 없다. 두 값을 섞어 매출·재고를 집계하면 결과가 통째로 무의미해진다.
pairs = [(UNIT[c["unit_id"]]["price"], c["price"]) for c in ctrs
         if c["unit_id"] in UNIT and c["price"] and UNIT[c["unit_id"]]["price"]]
n = len(pairs)
mx = sum(a for a, _ in pairs) / n
my = sum(b for _, b in pairs) / n
cov = sum((a - mx) * (b - my) for a, b in pairs) / n
sx = math.sqrt(sum((a - mx) ** 2 for a, _ in pairs) / n)
sy = math.sqrt(sum((b - my) ** 2 for _, b in pairs) / n)
CORR = round(cov / (sx * sy), 3)


def corr(xy):
    n = len(xy)
    mx = sum(a for a, _ in xy) / n
    my = sum(b for _, b in xy) / n
    sx = math.sqrt(sum((a - mx) ** 2 for a, _ in xy) / n)
    sy = math.sqrt(sum((b - my) ** 2 for _, b in xy) / n)
    return round(sum((a - mx) * (b - my) for a, b in xy) / n / (sx * sy), 3)


CORR_AREA = corr([(UNIT[c["unit_id"]]["area_m2"], c["price"]) for c in ctrs
                  if c["unit_id"] in UNIT and c["price"] and UNIT[c["unit_id"]]["area_m2"]])

# 평당가로 환산해 어느 쪽이 비현실적인지 판정 (1평 = 3.3058m2)
listed_pyeong = round(statistics.median([u["price"] / u["area_m2"] * 3.3058
                                         for u in units if u["price"] and u["area_m2"]]))
ctr_pyeong = round(statistics.median([c["price"] / UNIT[c["unit_id"]]["area_m2"] * 3.3058
                                      for c in ctrs if c["price"] and c["unit_id"] in UNIT]))

# 타입별 실계약가 중앙값으로 마스터 표시가를 캘리브레이션
ctr_by_type = collections.defaultdict(list)
for c in ctrs:
    if c["price"] and c["unit_id"] in UNIT:
        ctr_by_type[UNIT[c["unit_id"]]["type"]].append(c["price"])
listed_by_type = collections.defaultdict(list)
for u in units:
    if u["price"]:
        listed_by_type[u["type"]].append(u["price"])
CAL = {}
for t in listed_by_type:
    if ctr_by_type.get(t):
        CAL[t] = statistics.median(ctr_by_type[t]) / statistics.median(listed_by_type[t])
    else:
        CAL[t] = 1.0
for u in units:
    u["price_listed"] = u["price"]
    u["price_ref"] = round(u["price"] * CAL.get(u["type"], 1.0)) if u["price"] else None
    u["price_flag"] = "타입별 실계약가 중앙값으로 캘리브레이션(×%.3f)" % CAL.get(u["type"], 1.0)

TYPE_REF = {t: round(statistics.median(v)) for t, v in ctr_by_type.items()}   # 실거래 기준가
for t in listed_by_type:
    TYPE_REF.setdefault(t, round(statistics.median(listed_by_type[t])))
TYPE_AVG = TYPE_REF
TYPE_MIN = {}
for u in units:
    if u["price_ref"]:
        TYPE_MIN[u["type"]] = min(TYPE_MIN.get(u["type"], 10 ** 9), u["price_ref"])

price_scale = {
    "corr": CORR,
    "listed_pyeong": listed_pyeong,
    "contract_pyeong": ctr_pyeong,
    "ratio": round(listed_pyeong / ctr_pyeong, 1),
    "pairs": n,
    "corr_area": CORR_AREA,
    "cal": {t: round(v, 3) for t, v in sorted(CAL.items())},
    "type_ref": {t: TYPE_REF[t] for t in sorted(TYPE_REF)},
    "listed_med": {t: round(statistics.median(v)) for t, v in sorted(listed_by_type.items())},
}

# 분양가 0원이었던 계약을 동일 타입 실계약가 중앙값으로 보정
for c in ctrs:
    if c["price"] is None and c["price_flag"]:
        c["price"] = TYPE_REF.get(UNIT[c["unit_id"]]["type"]) if c["unit_id"] in UNIT else None

log("cross", "units.price ↔ contracts.price", "price_scale",
    "두 표의 가격 스케일 불일치 확인 → 매출은 실계약가만 사용, 재고는 타입별 실계약가로 캘리브레이션",
    len(units),
    "상관계수 %.3f (사실상 무관). 마스터 표시가는 평당 %s만원(비현실적), 실계약가는 평당 %s만원. "
    "두 값을 합산하면 매출이 %.1f배 부풀려짐" % (CORR, f"{listed_pyeong:,}", f"{ctr_pyeong:,}", listed_pyeong / ctr_pyeong),
    "high")

# ─────────────────────────────────────────────────────────────
# 6. 파생 · 매출 누수 진단
# ─────────────────────────────────────────────────────────────
ASOF = max([d for d in [cons_max] + [c["contract_date"] for c in ctrs if c["contract_date"]] if d])
ASOF_D = datetime.date.fromisoformat(ASOF)

cons_by_cust = collections.defaultdict(list)
for c in cons:
    if c["customer_id"]:
        cons_by_cust[c["customer_id"]].append(c)

ctr_by_cust = collections.defaultdict(list)
for c in ctrs:
    ctr_by_cust[c["customer_id"]].append(c)

live_contract_custs = {c["customer_id"] for c in ctrs if c["status"] in ("완료", "진행중")}

# 실현 매출
rev_done = sum(c["price"] or 0 for c in ctrs if c["status"] == "완료")
rev_prog = sum(c["price"] or 0 for c in ctrs if c["status"] == "진행중")

# --- 누수 1: 해지 계약 (확정 손실)
lk_cancel = [c for c in ctrs if c["status"] == "해지"]
lk1 = sum(c["price"] or 0 for c in lk_cancel)

# 기준 전환율: 계약희망 상담을 한 고객 중 실제 계약으로 이어진 비율
hope_custs = {c["customer_id"] for c in cons if c["customer_id"] and c["outcome"] == "계약희망"}
hope_won = hope_custs & live_contract_custs
CONV = (len(hope_won) / len(hope_custs)) if hope_custs else 0.0

# --- 누수 2: 계약희망 후 미전환 (기대매출)
lk2_list = []
for cid in sorted(hope_custs - live_contract_custs):
    cu = CUST[cid]
    p = TYPE_AVG.get(cu["interest_type"], 0)
    lk2_list.append({"customer_id": cid, "type": cu["interest_type"], "exp": p})
lk2 = round(sum(x["exp"] for x in lk2_list) * CONV)

# --- 누수 3: 재상담예정 방치 (마지막 상담 후 90일 초과 · 후속 없음)
lk3_list = []
for cid, lst in cons_by_cust.items():
    if cid in live_contract_custs:
        continue
    dated = [c for c in lst if c["consult_date"]]
    if not dated:
        continue
    last = max(dated, key=lambda c: c["consult_date"])
    if last["outcome"] != "재상담예정":
        continue
    gap = (ASOF_D - datetime.date.fromisoformat(last["consult_date"])).days
    if gap > 90:
        cu = CUST[cid]
        lk3_list.append({"customer_id": cid, "days": gap,
                         "exp": TYPE_AVG.get(cu["interest_type"], 0)})
lk3 = round(sum(x["exp"] for x in lk3_list) * CONV * 0.5)   # 방치 페널티 50%

# --- 누수 4: 추적 불가 리드 (orphan)
orphan_cons = [c for c in cons if c["orphan"]]
orphan_hope = [c for c in orphan_cons if c["outcome"] == "계약희망"]
avg_unit_price = round(statistics.median([c["price"] for c in ctrs if c["price"]]))
lk4 = round(len(orphan_hope) * avg_unit_price * CONV)

# --- 누수 5: 미계약 재고
unsold = [u for u in units if u["status"] in ("미계약", "보류")]
lk5 = sum(u["price_ref"] or 0 for u in unsold)

# --- 누수 6: 저효율 광고 (겹치는 기간만)
AD_END = ad_max
src_of = {c["customer_id"]: c["lead_source"] for c in customers}
ch_stat = {}
for a in ads:
    s = ch_stat.setdefault(a["channel"], {"spend": 0, "leads": 0, "clicks": 0, "impressions": 0})
    s["spend"] += a["spend"] or 0
    s["leads"] += a["leads"] or 0
    s["clicks"] += a["clicks"] or 0
    s["impressions"] += a["impressions"] or 0
for ch, s in ch_stat.items():
    s["contracts"] = 0
    s["revenue"] = 0
    s["customers"] = 0
for c in customers:
    ch = c["lead_source"]
    if ch in ch_stat:
        ch_stat[ch]["customers"] += 1
for ct in ctrs:
    if ct["status"] not in ("완료", "진행중"):
        continue
    if ct["contract_date"] and ct["contract_date"][:7] > AD_END:
        continue
    ch = src_of.get(ct["customer_id"])
    if ch in ch_stat:
        ch_stat[ch]["contracts"] += 1
        ch_stat[ch]["revenue"] += ct["price"] or 0
channels = []
for ch, s in sorted(ch_stat.items(), key=lambda kv: -kv[1]["spend"]):
    cac = round(s["spend"] / s["contracts"]) if s["contracts"] else None
    roas = round(s["revenue"] / s["spend"], 1) if s["spend"] else None
    channels.append({"channel": ch, **s, "cac": cac, "roas": roas,
                     "cpl": round(s["spend"] / s["leads"], 2) if s["leads"] else None})
med_roas = sorted([c["roas"] for c in channels if c["roas"] is not None])
MED = med_roas[len(med_roas) // 2] if med_roas else 0
lk6 = 0
weak = []
for c in channels:
    if c["roas"] is not None and c["roas"] < MED:
        # 중앙값 ROAS 수준까지 끌어올릴 때 회수 가능한 예산 = 격차분
        waste = round(c["spend"] * (1 - c["roas"] / MED)) if MED else 0
        lk6 += waste
        weak.append({"channel": c["channel"], "waste": waste, "roas": c["roas"]})

leaks = [
    {"key": "cancel", "label": "해지된 계약", "amount": lk1, "count": len(lk_cancel),
     "kind": "확정 손실", "basis": "status='해지' 계약 %d건의 분양가 합계" % len(lk_cancel),
     "action": "해지 사유 코드화 → 해지 발생 상위 상담원·타입 집중 코칭"},
    {"key": "hope", "label": "계약희망 후 미전환", "amount": lk2, "count": len(lk2_list),
     "kind": "기회 손실", "basis": "계약희망 이력 보유 미계약 고객 %d명 × 관심타입 평균분양가 × 기준전환율 %.1f%%" % (len(lk2_list), CONV * 100),
     "action": "액션 큐 상위부터 7일 내 재접촉 — 본 대시보드 하단 TOP 30"},
    {"key": "stale", "label": "재상담예정 90일 방치", "amount": lk3, "count": len(lk3_list),
     "kind": "기회 손실", "basis": "마지막 상담이 '재상담예정'이며 %s 기준 90일 초과 경과한 %d명 (방치 페널티 50%% 반영)" % (ASOF, len(lk3_list)),
     "action": "CRM에 'D+30 자동 리마인드' 규칙 신설 — 방치 자체를 구조적으로 차단"},
    {"key": "orphan", "label": "추적 불가 리드", "amount": lk4, "count": len(orphan_cons),
     "kind": "관리 실패", "basis": "고객·매물 마스터에 없는 ID로 기록된 상담 %d건, 그중 계약희망 %d건 × 실계약가 중앙값 × 기준전환율" % (len(orphan_cons), len(orphan_hope)),
     "action": "상담 입력 폼에 마스터 조회 필수화(자유 입력 차단) — 신규 발생 0건화"},
    {"key": "stock", "label": "미계약 재고", "amount": lk5, "count": len(unsold),
     "kind": "미실현", "basis": "status가 미계약·보류인 %d세대 × 타입별 실계약가 중앙값(마스터 표시가는 스케일 오류로 미사용)" % len(unsold),
     "action": "예산 적합 고객과 자동 매칭(타입·가격 기준) 후 타깃 제안"},
    {"key": "adwaste", "label": "저효율 광고 예산", "amount": lk6, "count": len(weak),
     "kind": "비용 낭비", "basis": "ROAS 중앙값(%.1f) 미달 채널 %d개의 격차분 예산" % (MED, len(weak)),
     "action": "미달 채널 예산을 ROAS 상위 채널로 재배분 (시뮬레이터 참조)"},
]
leak_total = sum(l["amount"] for l in leaks)

# ─────────────────────────────────────────────────────────────
# 7. 액션 큐 (재접촉 우선순위 스코어)
# ─────────────────────────────────────────────────────────────
GRADE_PT = {"VVIP": 15, "VIP": 12, "일반": 6, "신규": 5, "관심고객": 3}
_bud = sorted(c["budget"] for c in customers if c["budget"] is not None)
BUDGET_PCT = {b: (i + 1) / len(_bud) for i, b in enumerate(_bud)}
STATUS_PT = {"활성": 10, "휴면": 3, "이탈": 0}
avail_by_type = collections.defaultdict(list)
for u in units:
    if u["status"] in ("미계약", "보류"):
        avail_by_type[u["type"]].append(u)

queue = []
for cid, cu in CUST.items():
    if cid in live_contract_custs or cu["dup_person_of"]:
        continue
    lst = cons_by_cust.get(cid, [])
    if not lst:
        continue
    hope = sum(1 for c in lst if c["outcome"] == "계약희망")
    reject = sum(1 for c in lst if c["outcome"] == "거절")
    dated = [c for c in lst if c["consult_date"]]
    if not dated:
        continue
    last = max(dated, key=lambda c: c["consult_date"])
    days = (ASOF_D - datetime.date.fromisoformat(last["consult_date"])).days
    total_min = sum(min(c["duration_min"], 120) for c in lst if c["duration_min"])

    parts = {}
    parts["계약희망 이력"] = min(hope * 18, 40)
    parts["최근 접촉"] = 20 if days <= 30 else 14 if days <= 60 else 8 if days <= 90 else 3 if days <= 180 else 0
    parts["고객 등급"] = GRADE_PT.get(cu["grade"], 4)
    # 예산은 고객 표(만원)와 계약 표(만원)의 스케일이 서로 달라 절대 비교가 불가(§5.5).
    # 따라서 '전체 고객 대비 상대 예산력 백분위'로 점수화한다.
    if cu["budget"] is None:
        parts["예산 여력"] = 5
        fit = "예산 미기재"
        pct = None
    else:
        pct = BUDGET_PCT[cu["budget"]]
        parts["예산 여력"] = round(15 * pct)
        fit = "상위 %d%%" % round((1 - pct) * 100) if pct >= 0.5 else "하위 %d%%" % round(pct * 100)
    parts["상담 몰입도"] = 10 if total_min >= 60 else 6 if total_min >= 30 else 2
    parts["고객 상태"] = STATUS_PT.get(cu["status"], 0)
    parts["거절 이력"] = -8 * reject
    score = max(0, min(100, sum(parts.values())))

    # 매칭 매물: 관심타입 · 예산 이내 · 미계약 중 최고가
    cands = avail_by_type.get(cu["interest_type"], [])
    if cu["budget"]:
        ok = [u for u in cands if u["price_ref"] and u["price_ref"] <= cu["budget"]]
    else:
        ok = cands
    match = max(ok, key=lambda u: u["price_ref"] or 0) if ok else None

    if hope >= 2:
        act = "즉시 계약 클로징 콜 — 계약희망 %d회 반복" % hope
    elif hope == 1 and days > 60:
        act = "계약희망 후 %d일 방치 — 재접촉 후 조건 재제시" % days
    elif pct is not None and pct >= 0.75:
        act = "예산 상위 25%% — %s 상위층·선호향 업셀 제안" % cu["interest_type"]
    elif cu["status"] == "휴면":
        act = "휴면 복귀 캠페인 대상 — 혜택 안내 후 방문 유도"
    else:
        act = "%d일 경과 정기 팔로업" % days

    queue.append({
        "customer_id": cid, "name": cu["name"], "grade": cu["grade"],
        "region": cu["region"], "status": cu["status"], "phone": cu["phone"],
        "lead_source": cu["lead_source"], "interest_type": cu["interest_type"],
        "budget": cu["budget"], "budget_pct": round((pct or 0) * 100), "hope": hope, "reject": reject,
        "consults": len(lst), "last_date": last["consult_date"], "days_since": days,
        "total_min": total_min, "score": score, "parts": parts, "fit": fit,
        "action": act,
        "match_unit": match["unit_id"] if match else None,
        "match_price": match["price_ref"] if match else None,
        "exp_value": TYPE_AVG.get(cu["interest_type"], 0),
    })
queue.sort(key=lambda x: (-x["score"], -x["exp_value"]))
TOP = queue[:30]
top_value = round(sum(q["exp_value"] for q in TOP) * CONV)

# ─────────────────────────────────────────────────────────────
# 8. 차트용 집계
# ─────────────────────────────────────────────────────────────
# 월별 상담 · 계약 · 매출
months = sorted({c["consult_date"][:7] for c in cons if c["consult_date"]} |
                {c["contract_date"][:7] for c in ctrs if c["contract_date"]})
monthly = []
for m in months:
    cc = sum(1 for c in cons if c["consult_date"] and c["consult_date"][:7] == m)
    hp = sum(1 for c in cons if c["consult_date"] and c["consult_date"][:7] == m and c["outcome"] == "계약희망")
    kk = [c for c in ctrs if c["contract_date"] and c["contract_date"][:7] == m]
    monthly.append({"month": m, "consults": cc, "hopes": hp,
                    "contracts": len([k for k in kk if k["status"] in ("완료", "진행중")]),
                    "cancels": len([k for k in kk if k["status"] == "해지"]),
                    "revenue": sum(k["price"] or 0 for k in kk if k["status"] == "완료")})

# 퍼널
funnel_leads = sum(a["leads"] or 0 for a in ads)
outcome_cnt = collections.Counter(c["outcome"] for c in cons)
log("cross", "ad_spend.leads ↔ customers", "lead_gap",
    "광고 리드 %s건 대비 등록 고객 %d명(%.2f%%) — 리드→고객 전환 구간의 기록이 누락됨" % (
        f"{funnel_leads:,}", len(customers), len(customers) / funnel_leads * 100),
    funnel_leads,
    "광고 테이블의 leads는 클릭·문의 등 원시 반응 수치로 보이며, 고객 마스터와 1:1 대응하지 않는다. "
    "퍼널은 '고객 등록' 이후만 신뢰 구간으로 사용하고 광고 리드는 참고값으로 분리 표기", "high")

funnel = [
    {"stage": "고객 등록", "value": len(customers), "note": "중복 제거 후 실제 고객 수 (광고 리드 %s건은 대응 관계 불명 — 참고값)" % f"{funnel_leads:,}"},
    {"stage": "상담 진행", "value": len({c["customer_id"] for c in cons if c["customer_id"]}), "note": "1회 이상 상담한 고객"},
    {"stage": "계약희망", "value": len(hope_custs), "note": "상담결과 '계약희망' 도달"},
    {"stage": "계약 체결", "value": len(live_contract_custs), "note": "완료 + 진행중"},
    {"stage": "계약 완료", "value": len({c["customer_id"] for c in ctrs if c["status"] == "완료"}), "note": "잔금까지 종결"},
]

# 상담원 성과
rep = {}
for c in cons:
    r = rep.setdefault(c["consultant"], {"consultant": c["consultant"], "consults": 0, "hopes": 0,
                                         "minutes": 0, "custs": set()})
    r["consults"] += 1
    r["hopes"] += 1 if c["outcome"] == "계약희망" else 0
    r["minutes"] += min(c["duration_min"], 120) if c["duration_min"] else 0
    if c["customer_id"]:
        r["custs"].add(c["customer_id"])
reps = []
for r in rep.values():
    won = len(r["custs"] & live_contract_custs)
    reps.append({"consultant": r["consultant"], "consults": r["consults"], "hopes": r["hopes"],
                 "customers": len(r["custs"]), "won": won,
                 "hope_rate": round(r["hopes"] / r["consults"] * 100, 1),
                 "win_rate": round(won / len(r["custs"]) * 100, 1) if r["custs"] else 0,
                 "avg_min": round(r["minutes"] / r["consults"], 1)})
reps.sort(key=lambda x: -x["win_rate"])

# 영업사원(계약) 성과
srep = {}
for c in ctrs:
    s = srep.setdefault(c["sales_rep"], {"rep": c["sales_rep"], "total": 0, "done": 0, "cancel": 0, "revenue": 0})
    s["total"] += 1
    s["done"] += 1 if c["status"] == "완료" else 0
    s["cancel"] += 1 if c["status"] == "해지" else 0
    s["revenue"] += (c["price"] or 0) if c["status"] == "완료" else 0
sreps = sorted(srep.values(), key=lambda x: -x["revenue"])
for s in sreps:
    s["cancel_rate"] = round(s["cancel"] / s["total"] * 100, 1)

# 타입별 재고 · 소진율
type_stat = {}
for u in units:
    t = type_stat.setdefault(u["type"], {"type": u["type"], "total": 0, "sold": 0, "unsold": 0,
                                         "hold": 0, "reserved": 0, "stock_value": 0, "avg_price": 0, "_p": []})
    t["total"] += 1
    t["_p"].append(u["price_ref"] or 0)
    if u["status"] == "계약완료": t["sold"] += 1
    elif u["status"] == "미계약": t["unsold"] += 1; t["stock_value"] += u["price_ref"] or 0
    elif u["status"] == "보류": t["hold"] += 1; t["stock_value"] += u["price_ref"] or 0
    else: t["reserved"] += 1
demand = collections.Counter(c["interest_type"] for c in customers)
types = []
for t in type_stat.values():
    t["avg_price"] = round(sum(t["_p"]) / len(t["_p"]))
    t.pop("_p")
    t["sold_rate"] = round(t["sold"] / t["total"] * 100, 1)
    t["demand"] = demand.get(t["type"], 0)
    t["demand_per_stock"] = round(t["demand"] / t["unsold"], 2) if t["unsold"] else None
    types.append(t)
types.sort(key=lambda x: -x["demand"])

# 지역별
region_stat = collections.Counter(c["region"] for c in customers)
region_won = collections.Counter(CUST[c["customer_id"]]["region"] for c in ctrs
                                 if c["status"] in ("완료", "진행중") and c["customer_id"] in CUST)
regions = sorted([{"region": r, "customers": n, "won": region_won.get(r, 0),
                   "rate": round(region_won.get(r, 0) / n * 100, 1)}
                  for r, n in region_stat.items()], key=lambda x: -x["customers"])

# 예산 vs 관심타입 가격 미스매치
mismatch = []
for c in customers:
    tmin = TYPE_MIN.get(c["interest_type"])
    if c["budget"] and tmin:
        mismatch.append({"budget": c["budget"], "min_price": tmin, "type": c["interest_type"],
                         "gap": c["budget"] - tmin, "grade": c["grade"]})
gap_short = [m for m in mismatch if m["gap"] < 0]

# 데이터 품질 지표
total_cells_raw = len(raw_cust) * 11 + len(raw_cons) * 9 + len(raw_ctr) * 9 + len(raw_units) * 10 + len(raw_ad) * 6
issues_fixed = sum(l["count"] for l in LOG if l["rule"] not in ("units_passthrough", "ad_passthrough"))
quality = {
    "raw_rows": len(raw_cust) + len(raw_cons) + len(raw_ctr) + len(raw_units) + len(raw_ad),
    "clean_rows": len(customers) + len(cons) + len(ctrs) + len(units) + len(ads),
    "cells": total_cells_raw,
    "issues": issues_fixed,
    "issue_rate": round(issues_fixed / total_cells_raw * 100, 2),
    "before": {
        "고객 등급 표기": 9, "지역 표기": 24, "상담 채널 표기": 8,
        "계약 상태 표기": 6, "날짜 포맷": 4, "전화 포맷": 3,
    },
    "after": {
        "고객 등급 표기": 5, "지역 표기": 17, "상담 채널 표기": 4,
        "계약 상태 표기": 3, "날짜 포맷": 1, "전화 포맷": 1,
    },
    "integrity": {
        "고객 ID 중복": cnt["dup_id"], "동일인 의심": sum(len(v) - 1 for v in cross_dup.values()),
        "고아 상담(고객)": c2["orphan_cust"], "고아 상담(매물)": c2["orphan_unit"],
        "예산 sentinel": cnt["budget_sentinel"], "대출금 결측": c3["loan_missing"],
        "분양가 0원": c3["price0"], "상담시간 이상": c2["dur_nonpos"] + c2["dur_high"],
        "이메일 무효": cnt["email_nonascii"] + cnt["email_missing"], "전화 결측": cnt["phone_missing"],
    },
}

# 인사이트 (발견 → 근거 → 액션)
def eok(v):
    return round(v / 10000, 1)

top_leak = max(leaks, key=lambda l: l["amount"] if l["key"] != "stock" else 0)
best_ch = max([c for c in channels if c["roas"] is not None], key=lambda c: c["roas"])
worst_ch = min([c for c in channels if c["roas"] is not None], key=lambda c: c["roas"])
hot_type = max(types, key=lambda t: t["demand_per_stock"] or 0)
cold_type = min([t for t in types if t["unsold"] > 0], key=lambda t: t["demand_per_stock"] or 99)
best_rep = reps[0]
worst_rep = reps[-1]
worst_srep = max(sreps, key=lambda s: s["cancel_rate"])

insights = [
    {
        "id": "I0", "tag": "정합성",
        "finding": "매물 마스터의 분양가와 실제 계약금액은 상관계수 %.3f — 서로 다른 가격 체계다. 두 표를 그대로 합산하면 매출이 %.1f배 부풀려진다." % (CORR, price_scale["ratio"]),
        "evidence": "계약 %d건을 매물과 조인해 비교. 마스터 표시가는 평당 %s만원(전국 최고가 아파트를 넘는 비현실적 수치), 실계약가는 평당 %s만원. 계약금액은 전용면적과의 상관계수도 %.3f로 무관하다. 예: 102A형 마스터 중앙값 %s만원 vs 실계약 중앙값 %s만원." % (
            price_scale["pairs"], f"{listed_pyeong:,}", f"{ctr_pyeong:,}", CORR_AREA,
            f"{price_scale['listed_med']['102A']:,}", f"{price_scale['type_ref']['102A']:,}"),
        "action": "매출·재고 금액은 **실계약가만** 근거로 삼고, 마스터 표시가는 `price_listed`로 분리 보관. 재고 평가액은 타입별 실계약가 중앙값으로 캘리브레이션(계수 %.3f~%.3f)해 산출. 원천 시스템에는 가격 단위 정의서와 CHECK 제약을 요구." % (min(price_scale["cal"].values()), max(price_scale["cal"].values())),
        "metric": "r = %.3f" % CORR,
    },
    {
        "id": "I1", "tag": "누수",
        "finding": "'계약희망'까지 도달하고 사라진 고객이 %d명 — 단일 항목 중 가장 큰 누수" % len(lk2_list),
        "evidence": "계약희망 이력 고객 %d명 중 실제 계약 %d명(전환율 %.1f%%). 미전환 %d명에 관심타입 평균 분양가와 이 전환율을 적용하면 %s억." % (
            len(hope_custs), len(hope_won), CONV * 100, len(lk2_list), eok(lk2)),
        "action": "액션 큐 상위 30명(기대 %s억)부터 7일 내 클로징 콜. 스코어 60점 이상은 팀장 직접 배정." % eok(top_value),
        "metric": "%s억" % eok(lk2),
    },
    {
        "id": "I2", "tag": "구조",
        "finding": "상담 %d건이 존재하지 않는 고객·매물 ID로 기록돼 후속 조치 자체가 불가능" % len(orphan_cons),
        "evidence": "고객 마스터 미존재 %d건 · 매물 마스터 미존재 %d건. 이 중 %d건은 결과가 '계약희망'이었고, 평균 분양가·기준전환율 적용 시 %s억 상당." % (
            c2["orphan_cust"], c2["orphan_unit"], len(orphan_hope), eok(lk4)),
        "action": "상담 입력 폼의 고객·매물 필드를 자유 입력에서 마스터 조회(FK)로 전환. 기존 %d건은 이름·연락처 역매칭 배치로 복구 시도." % len(orphan_cons),
        "metric": "%d건" % len(orphan_cons),
    },
    {
        "id": "I3", "tag": "ROI",
        "finding": "채널별 ROAS 격차가 %.1f배 — 예산은 성과와 반대로 배분되어 있음" % (best_ch["roas"] / worst_ch["roas"] if worst_ch["roas"] else 0),
        "evidence": "%s ROAS %.1f (지출 %s억 · 계약 %d건) vs %s ROAS %.1f (지출 %s억 · 계약 %d건). ROAS 중앙값 미달 채널의 격차분만 %s억." % (
            best_ch["channel"], best_ch["roas"], eok(best_ch["spend"]), best_ch["contracts"],
            worst_ch["channel"], worst_ch["roas"], eok(worst_ch["spend"]), worst_ch["contracts"], eok(lk6)),
        "action": "%s 예산의 30%%를 %s로 이전하고 8주간 CAC 추적. 대시보드의 예산 재배분 시뮬레이터로 사전 검증." % (worst_ch["channel"], best_ch["channel"]),
        "metric": "ROAS %.1f ↔ %.1f" % (best_ch["roas"], worst_ch["roas"]),
    },
    {
        "id": "I4", "tag": "재고",
        "finding": "수요와 재고가 어긋나 있음 — %s는 재고 대비 수요 %.1f배, %s는 %.1f배" % (
            hot_type["type"], hot_type["demand_per_stock"] or 0, cold_type["type"], cold_type["demand_per_stock"] or 0),
        "evidence": "%s: 관심고객 %d명 / 미계약 %d세대, 소진율 %.1f%%. %s: 관심고객 %d명 / 미계약 %d세대, 재고금액 %s억." % (
            hot_type["type"], hot_type["demand"], hot_type["unsold"], hot_type["sold_rate"],
            cold_type["type"], cold_type["demand"], cold_type["unsold"], eok(cold_type["stock_value"])),
        "action": "%s 대기 수요를 %s 상위층·선호 향(남향/공원뷰)으로 유도하는 업셀 스크립트 배포." % (hot_type["type"], cold_type["type"]),
        "metric": "%s억 재고" % eok(lk5),
    },
    {
        "id": "I5", "tag": "사람",
        "finding": "상담원 간 계약 전환율이 %.1f%%p 벌어져 있고, 해지율도 담당자별로 갈림" % (best_rep["win_rate"] - worst_rep["win_rate"]),
        "evidence": "최고 %s %.1f%% (담당 %d명 중 %d명 계약) vs 최저 %s %.1f%%. 계약 담당 기준 해지율은 %s가 %.1f%%로 가장 높음." % (
            best_rep["consultant"], best_rep["win_rate"], best_rep["customers"], best_rep["won"],
            worst_rep["consultant"], worst_rep["win_rate"], worst_srep["rep"], worst_srep["cancel_rate"]),
        "action": "상위 상담원의 '계약희망 → 계약' 구간 통화 스크립트를 표준화해 전사 배포. 해지 상위 담당자는 계약 전 조건 확인 체크리스트 의무화.",
        "metric": "%.1f%%p 격차" % (best_rep["win_rate"] - worst_rep["win_rate"]),
    },
    {
        "id": "I6", "tag": "품질",
        "finding": "원본 그대로 집계하면 등급·채널·상태가 %d종으로 흩어져 어떤 지표도 신뢰할 수 없었음" % (9 + 8 + 6),
        "evidence": "정리 전 등급 9종·상담채널 8종·계약상태 6종·날짜 4종. 총 %d건의 값을 %d개 규칙으로 표준화(전체 셀의 %.2f%%). 고객 %d행 → %d행." % (
            issues_fixed, len([l for l in LOG if l['count'] > 0]), quality["issue_rate"], len(raw_cust), len(customers)),
        "action": "표준화 규칙을 DB 제약조건(CHECK · FK · UNIQUE)으로 승격해 다음 데이터부터는 입력 단계에서 차단. schema.sql에 반영 완료.",
        "metric": "%.2f%% 오류율" % quality["issue_rate"],
    },
]

dataset = {
    "meta": {
        "generated_from": "Track 2 Mission 합성 데이터셋",
        "asof": ASOF,
        "unit": "만원",
        "conv_rate": round(CONV * 100, 1),
        "note": "모든 데이터는 합성(가상) 데이터입니다.",
    },
    "kpi": {
        "customers": len(customers),
        "customers_raw": len(raw_cust),
        "consults": len(cons),
        "contracts": len(ctrs),
        "contracts_live": len([c for c in ctrs if c["status"] in ("완료", "진행중")]),
        "revenue_done": rev_done,
        "revenue_prog": rev_prog,
        "leak_total": leak_total,
        "leak_ratio": round(leak_total / (rev_done + leak_total) * 100, 1),
        "conv_rate": round(CONV * 100, 1),
        "units": len(units),
        "unsold": len(unsold),
        "queue_value": top_value,
        "avg_price": avg_unit_price,
    },
    "leaks": leaks,
    "monthly": monthly,
    "funnel": funnel,
    "ad_leads": funnel_leads,
    "channels": channels,
    "weak_channels": weak,
    "median_roas": MED,
    "types": types,
    "regions": regions,
    "reps": reps,
    "sales_reps": sreps,
    "queue": TOP,
    "queue_all": len(queue),
    "quality": quality,
    "price_scale": price_scale,
    "insights": insights,
    "outcomes": [{"outcome": k, "count": v} for k, v in outcome_cnt.most_common()],
    "mismatch": {"total": len(mismatch), "short": len(gap_short),
                 "points": [{"b": m["budget"], "p": m["min_price"], "t": m["type"]} for m in mismatch]},
}

# ─────────────────────────────────────────────────────────────
# 9. 파일 출력
# ─────────────────────────────────────────────────────────────
def write_csv(name, rows, cols):
    with open(os.path.join(CLEAN, name), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

write_csv("customers_clean.csv", customers,
          ["customer_id", "name", "phone", "email", "email_valid", "grade", "lead_source",
           "lead_source_conf", "interest_type", "signup_date", "region", "budget", "status", "dup_person_of"])
write_csv("units_clean.csv", units,
          ["unit_id", "building", "floor", "unit_no", "type", "area_m2", "price_listed", "price_ref",
           "price_flag", "status", "direction", "view"])
write_csv("consultations_clean.csv", cons,
          ["consult_id", "customer_id", "customer_id_raw", "unit_id", "unit_id_raw", "consultant",
           "channel", "consult_date", "duration_min", "duration_flag", "outcome", "notes", "orphan"])
write_csv("contracts_clean.csv", ctrs,
          ["contract_id", "customer_id", "unit_id", "contract_date", "price", "price_flag",
           "down_payment", "loan_amount", "loan_flag", "status", "sales_rep"])
write_csv("ad_spend_clean.csv", ads, ["month", "channel", "spend", "impressions", "clicks", "leads"])

with open(os.path.join(OUT, "dataset.json"), "w", encoding="utf-8") as f:
    json.dump(dataset, f, ensure_ascii=False, separators=(",", ":"))

with open(os.path.join(OUT, "cleaning_log.json"), "w", encoding="utf-8") as f:
    json.dump({"asof": ASOF, "rules": LOG, "quality": quality}, f, ensure_ascii=False, indent=1)

# Supabase 시드 SQL
with open(os.path.join(ROOT, "pipeline", "seed.sql"), "w", encoding="utf-8") as f:
    f.write("-- Track 2 · 정리 완료 데이터 시드 (schema.sql 실행 후 적용)\nBEGIN;\n")
    f.write("TRUNCATE contracts, consultations, ad_spend, customers, units RESTART IDENTITY CASCADE;\n\n")
    def dump(table, rows, cols):
        f.write(f"-- {table}: {len(rows)}행\n")
        for i in range(0, len(rows), 100):
            chunk = rows[i:i + 100]
            f.write(f"INSERT INTO {table} ({', '.join(cols)}) VALUES\n")
            f.write(",\n".join("(" + ", ".join(sq(r.get(c)) for c in cols) + ")" for r in chunk))
            f.write(";\n")
        f.write("\n")
    dump("units", units, ["unit_id", "building", "floor", "unit_no", "type", "area_m2",
                          "price_listed", "price_ref", "price_flag", "status", "direction", "view"])
    dump("customers", customers, ["customer_id", "name", "phone", "email", "email_valid", "grade",
                                  "lead_source", "lead_source_conf", "interest_type", "signup_date",
                                  "region", "budget", "status", "dup_person_of"])
    dump("consultations", cons, ["consult_id", "customer_id", "customer_id_raw", "unit_id", "unit_id_raw",
                                 "consultant", "channel", "consult_date", "duration_min", "duration_flag",
                                 "outcome", "notes", "orphan"])
    dump("contracts", ctrs, ["contract_id", "customer_id", "unit_id", "contract_date", "price",
                             "price_flag", "down_payment", "loan_amount", "loan_flag", "status", "sales_rep"])
    dump("ad_spend", ads, ["month", "channel", "spend", "impressions", "clicks", "leads"])
    f.write("COMMIT;\n")

# 처리 로그 마크다운
with open(os.path.join(ROOT, "docs", "CLEANING_LOG.md"), "w", encoding="utf-8") as f:
    f.write("# 데이터 처리 로그\n\n")
    f.write(f"- 생성: `python pipeline/clean.py`\n- 데이터 기준일: **{ASOF}**\n")
    f.write(f"- 원본 {quality['raw_rows']:,}행 → 정리 후 {quality['clean_rows']:,}행\n")
    f.write(f"- 표준화·보정한 값 **{issues_fixed:,}건** (전체 셀의 {quality['issue_rate']}%)\n\n")
    f.write("| 테이블 | 컬럼 | 처리 내용 | 건수 | 판단 근거 |\n|---|---|---|---:|---|\n")
    for l in LOG:
        f.write(f"| {l['table']} | `{l['field']}` | {l['action']} | {l['count']:,} | {l['note']} |\n")
    f.write("\n## 원칙\n\n")
    f.write("1. **삭제보다 플래그** — 이상치·결측은 지우지 않고 별도 컬럼에 사유를 남겨 추적 가능하게 함\n")
    f.write("2. **결측을 0으로 채우지 않음** — 대출금 결측을 0으로 채우면 '현금 완납'과 구분 불가\n")
    f.write("3. **추정에는 신뢰도 표기** — 유입경로 '광고' → '옥외광고' 매핑은 `lead_source_conf='추정'`\n")
    f.write("4. **재발 방지까지** — 표준화 규칙을 `pipeline/schema.sql`의 CHECK·FK 제약으로 승격\n")

print("OK")
print("  고객      %d행 → %d행" % (len(raw_cust), len(customers)))
print("  상담      %d행 (고아 고객 %d · 고아 매물 %d)" % (len(cons), c2["orphan_cust"], c2["orphan_unit"]))
print("  계약      %d행 (완료 %d · 진행중 %d · 해지 %d)" % (
    len(ctrs), len([c for c in ctrs if c["status"] == "완료"]),
    len([c for c in ctrs if c["status"] == "진행중"]), len(lk_cancel)))
print("  기준일    %s / 기준전환율 %.1f%%" % (ASOF, CONV * 100))
print("  총 누수   %s억 (실현매출 %s억)" % (eok(leak_total), eok(rev_done)))
for l in leaks:
    print("    - %-16s %8s억  (%d건)" % (l["label"], eok(l["amount"]), l["count"]))
print("  액션큐    %d명 중 TOP 30 (기대 %s억)" % (len(queue), eok(top_value)))
