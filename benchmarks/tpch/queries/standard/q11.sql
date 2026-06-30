select ps_partkey, sum(ps_supplycost * ps_availqty) as value
from {{schema}}.partsupp ps
join {{schema}}.supplier s on s.s_suppkey = ps.ps_suppkey
join {{schema}}.nation n on n.n_nationkey = s.s_nationkey
where n.n_name = 'GERMANY'
group by ps_partkey
having sum(ps_supplycost * ps_availqty) > (
  select sum(ps2.ps_supplycost * ps2.ps_availqty) * 0.0001
  from {{schema}}.partsupp ps2
  join {{schema}}.supplier s2 on s2.s_suppkey = ps2.ps_suppkey
  join {{schema}}.nation n2 on n2.n_nationkey = s2.s_nationkey
  where n2.n_name = 'GERMANY'
)
order by value desc;
