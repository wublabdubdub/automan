select 100.00 * sum(case when p.p_type like 'PROMO%' then l.l_extendedprice * (1 - l.l_discount) else 0 end)
       / sum(l.l_extendedprice * (1 - l.l_discount)) as promo_revenue
from {{schema}}.lineitem l
join {{schema}}.part p on p.p_partkey = l.l_partkey
where l.l_shipdate >= date '1995-09-01'
  and l.l_shipdate < date '1995-10-01';
