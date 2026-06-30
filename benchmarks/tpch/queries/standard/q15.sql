with revenue as (
  select l_suppkey as supplier_no,
         sum(l_extendedprice * (1 - l_discount)) as total_revenue
  from {{schema}}.lineitem
  where l_shipdate >= date '1996-01-01'
    and l_shipdate < date '1996-04-01'
  group by l_suppkey
)
select s.s_suppkey, s.s_name, s.s_address, s.s_phone, r.total_revenue
from {{schema}}.supplier s
join revenue r on s.s_suppkey = r.supplier_no
where r.total_revenue = (select max(total_revenue) from revenue)
order by s.s_suppkey;
