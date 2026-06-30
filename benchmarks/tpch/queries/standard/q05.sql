select n.n_name,
       sum(l.l_extendedprice * (1 - l.l_discount)) as revenue
from {{schema}}.customer c
join {{schema}}.orders o on c.c_custkey = o.o_custkey
join {{schema}}.lineitem l on l.l_orderkey = o.o_orderkey
join {{schema}}.supplier s on s.s_suppkey = l.l_suppkey
join {{schema}}.nation n on n.n_nationkey = s.s_nationkey
join {{schema}}.region r on r.r_regionkey = n.n_regionkey
where r.r_name = 'ASIA'
  and o.o_orderdate >= date '1994-01-01'
  and o.o_orderdate < date '1995-01-01'
group by n.n_name
order by revenue desc;
