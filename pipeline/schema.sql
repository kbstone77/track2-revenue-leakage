-- Track 2 · Supabase 스키마
-- 정리 단계에서 적용한 표준화 규칙을 DB 제약으로 승격해, 다음 데이터부터는 입력 시점에 차단한다.

drop table if exists contracts, consultations, ad_spend, customers, units cascade;

create table units (
  unit_id      text primary key,
  building     text not null,
  floor        int  check (floor between 1 and 100),
  unit_no      text,
  type         text not null,
  area_m2      int  check (area_m2 > 0),
  price_listed bigint,                     -- 원본 마스터 표시가 (스케일 이상 · 집계 사용 금지)
  price_ref    bigint,                     -- 타입별 실계약가로 캘리브레이션한 참조가
  price_flag   text,
  status       text not null check (status in ('계약완료','미계약','예약','보류')),
  direction    text,
  view         text
);

create table customers (
  customer_id      text primary key,       -- 원본의 ID 중복은 적재 전에 제거됨
  name             text not null,
  phone            text check (phone ~ '^01[0-9]-[0-9]{3,4}-[0-9]{4}$'),
  email            text,
  email_valid      boolean not null default false,
  grade            text not null check (grade in ('VVIP','VIP','일반','신규','관심고객')),
  lead_source      text not null check (lead_source in
                     ('블로그','SNS광고','포털검색광고','옥외광고','지인추천이벤트','전시관행사','직접유입')),
  lead_source_conf text not null default '확정' check (lead_source_conf in ('확정','추정')),
  interest_type    text,
  signup_date      date,
  region           text,
  budget           bigint check (budget is null or budget between 1 and 900000),  -- -1 / 999999 sentinel 차단
  status           text not null check (status in ('활성','휴면','이탈')),
  dup_person_of    text references customers(customer_id)
);

create table consultations (
  consult_id      text primary key,
  customer_id     text references customers(customer_id),   -- 고아 참조는 NULL + raw 보존
  customer_id_raw text,
  unit_id         text references units(unit_id),
  unit_id_raw     text,
  consultant      text not null,
  channel         text not null check (channel in ('전화','방문','SNS','온라인')),
  consult_date    date,
  duration_min    int check (duration_min is null or duration_min > 0),
  duration_flag   text,
  outcome         text not null check (outcome in ('계약희망','재상담예정','보류','거절')),
  notes           text,
  orphan          text
);

create table contracts (
  contract_id   text primary key,
  customer_id   text not null references customers(customer_id),
  unit_id       text not null references units(unit_id),
  contract_date date,
  price         bigint check (price > 0),                   -- 0원 계약 차단
  price_flag    text,
  down_payment  bigint,
  loan_amount   bigint,                                     -- 결측은 NULL 유지 (0으로 채우지 않음)
  loan_flag     text,
  status        text not null check (status in ('완료','진행중','해지')),
  sales_rep     text not null
);

create table ad_spend (
  month       text not null check (month ~ '^\d{4}-\d{2}$'),
  channel     text not null,
  spend       bigint, impressions bigint, clicks bigint, leads bigint,
  primary key (month, channel)
);

-- 동일 매물에 유효 계약은 1건만
create unique index contracts_live_unit on contracts(unit_id) where status in ('완료','진행중');

create index on consultations(customer_id);
create index on consultations(consult_date);
create index on contracts(customer_id);
create index on contracts(contract_date);
create index on customers(lead_source);
