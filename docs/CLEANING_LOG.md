# 데이터 처리 로그

- 생성: `python pipeline/clean.py`
- 데이터 기준일: **2026-12-28**
- 원본 3,862행 → 정리 후 3,814행
- 표준화·보정한 값 **142,598건** (전체 셀의 397.85%)

| 테이블 | 컬럼 | 처리 내용 | 건수 | 판단 근거 |
|---|---|---|---:|---|
| units | `-` | 기준 테이블 — 공백만 제거 후 그대로 사용 | 220 | 다른 표의 unit_id 유효성 검증 기준으로 사용 |
| customers | `customer_id` | 동일 customer_id 중복행 → 완전성 점수 높은 1건만 채택 | 48 | 전화 포맷·공백만 다른 완전 중복. 648행 → 600행 |
| customers | `name/phone` | 이름+전화 동일 · ID 다른 건 → 동일인 후보로 태깅 | 0 | 삭제하지 않고 dup_person_of로 연결 (오삭제 방지) |
| customers | `grade` | 등급 표기 통일 (vip/Vip/VIP, 앞뒤 공백) | 178 | VVIP·VIP·일반·신규·관심고객 5단계로 수렴 |
| customers | `region` | 지역 표기 통일 (부산시/Busan→부산 등) | 37 | 24종 → 17개 시도로 수렴 |
| customers | `lead_source` | 유입경로 → 광고비 테이블 채널명으로 매핑 | 515 | '광고'는 매체 미표기 → 옥외광고로 추정 매핑(conf=추정), ROI 해석 시 주의 |
| customers | `phone` | 전화번호 010-XXXX-XXXX 형식으로 통일 | 298 | 01012345678 / 010.1234.5678 3종 포맷 혼재 |
| customers | `phone` | 전화번호 결측 → NULL 유지 | 36 | 임의 값 대체 금지. 연락 불가 고객으로 별도 집계 |
| customers | `email` | 한글 로컬파트 이메일 → email_valid=false 플래그 | 180 | 값은 보존하되 발송 대상에서 제외 (삭제 시 원인 추적 불가) |
| customers | `email` | 이메일 결측 → NULL 유지 | 38 |  |
| customers | `budget_10k_krw` | 예산 -1 / 999999 → NULL (미상 sentinel) | 129 | 평균 예산 계산에서 제외. 미기재 129건은 예산 적합도 판정 불가로 처리 |
| customers | `signup_date` | 가입일 4종 포맷 → ISO(YYYY-MM-DD) 통일 | 172 |  |
| consultations | `consult_date` | 날짜 4종 포맷 → ISO 통일 | 637 | ISO(YYYY-MM-DD) 1863건 · 미국식(MM-DD-YYYY) 214건 · 슬래시(YYYY/MM/DD) 212건 · 구분자없음(YYYYMMDD) 211건 |
| consultations | `channel` | 채널 표기 통일 (phone/Phone/전화 · 방문상담/방문) | 1,288 | 8종 → 4종(전화·방문·SNS·온라인) |
| consultations | `outcome` | 상담결과 앞뒤 공백 제거 | 501 | '계약희망 '과 '계약희망'이 다른 값으로 집계되던 문제 |
| consultations | `duration_min` | 상담시간 0분 이하 → NULL | 493 | 음수(-5)는 물리적으로 불가 → 측정오류로 판정 |
| consultations | `duration_min` | 상담시간 120분 초과 → 값 보존 + 이상치 플래그 | 225 | 삭제하지 않음. 평균 산출 시에만 120분으로 winsorize |
| consultations | `customer_id` | 고객 마스터에 없는 customer_id → NULL 처리 후 격리 | 74 | 행을 지우지 않음 — '추적 불가 리드'로 누수 금액 산정에 사용 |
| consultations | `unit_id` | 매물 마스터에 없는 unit_id → NULL 처리 후 격리 | 83 | 매물별 집계에서만 제외, 상담 건수에는 포함 |
| contracts | `status` | 계약상태 표기 통일 (완료/complete/Completed) | 133 | 6종 → 3종(완료·진행중·해지) |
| contracts | `contract_date` | 계약일 4종 포맷 → ISO 통일 | 96 | ISO(YYYY-MM-DD) 254건 · 구분자없음(YYYYMMDD) 42건 · 미국식(MM-DD-YYYY) 28건 · 슬래시(YYYY/MM/DD) 26건 |
| contracts | `price_10k_krw` | 분양가 0원 → 동일 타입 실계약가 중앙값으로 대체 | 4 | 0원 계약은 존재할 수 없음. 마스터 표시가는 스케일이 달라 사용 불가(§5.5) → 대체 사실을 price_flag에 기록 |
| contracts | `loan_amount_10k_krw` | 대출금액 결측 → NULL 유지 | 41 | 0원으로 채우면 '현금 완납'과 구분 불가 → 평균 대출액에서 제외 |
| contracts | `unit_id` | 동일 매물에 유효계약 2건 이상 → 충돌 플래그 | 103 | 해지 후 재계약이 아닌 진짜 충돌만 카운트 |
| ad_spend | `-` | 정돈된 상태 — 타입 변환만 수행 | 144 | 2024-01 ~ 2025-12 (24개월 × 6채널) |
| cross | `기간` | 광고비 기간(~2025-12)과 상담 기간(~2026-12) 불일치 → ROI는 겹치는 구간만 산출 | 1 | 2026년 상담·계약은 채널 ROI 계산에서 제외해야 왜곡이 없음 |
| cross | `units.price ↔ contracts.price` | 두 표의 가격 스케일 불일치 확인 → 매출은 실계약가만 사용, 재고는 타입별 실계약가로 캘리브레이션 | 220 | 상관계수 -0.059 (사실상 무관). 마스터 표시가는 평당 9,298만원(비현실적), 실계약가는 평당 986만원. 두 값을 합산하면 매출이 9.4배 부풀려짐 |
| cross | `ad_spend.leads ↔ customers` | 광고 리드 137,068건 대비 등록 고객 600명(0.44%) — 리드→고객 전환 구간의 기록이 누락됨 | 137,068 | 광고 테이블의 leads는 클릭·문의 등 원시 반응 수치로 보이며, 고객 마스터와 1:1 대응하지 않는다. 퍼널은 '고객 등록' 이후만 신뢰 구간으로 사용하고 광고 리드는 참고값으로 분리 표기 |

## 원칙

1. **삭제보다 플래그** — 이상치·결측은 지우지 않고 별도 컬럼에 사유를 남겨 추적 가능하게 함
2. **결측을 0으로 채우지 않음** — 대출금 결측을 0으로 채우면 '현금 완납'과 구분 불가
3. **추정에는 신뢰도 표기** — 유입경로 '광고' → '옥외광고' 매핑은 `lead_source_conf='추정'`
4. **재발 방지까지** — 표준화 규칙을 `pipeline/schema.sql`의 CHECK·FK 제약으로 승격
