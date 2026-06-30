select nation, o_year, sum(amount) as sum_profit
from (
  select n.n_name as nation,
         extract(year from o.o_orderdate) as o_year,
         l.l_extendedprice * (1 - l.l_discount) - ps.ps_supplycost * l.l_quantity as amount
  from {{schema}}.part p
  join {{schema}}.lineitem l on p.p_partkey = l.l_partkey
  join {{schema}}.partsupp ps on ps.ps_partkey = l.l_partkey and ps.ps_suppkey = l.l_suppkey
  join {{schema}}.supplier s on s.s_suppkey = l.l_suppkey
  join {{schema}}.orders o on o.o_orderkey = l.l_orderkey
  join {{schema}}.nation n on s.s_nationkey = n.n_nationkey
  where p.p_name like '%green%'
) profit
group by nation, o_year
order by nation, o_year desc;
