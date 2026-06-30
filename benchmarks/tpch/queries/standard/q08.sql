select o_year,
       sum(case when nation = 'BRAZIL' then volume else 0 end) / sum(volume) as mkt_share
from (
  select extract(year from o.o_orderdate) as o_year,
         l.l_extendedprice * (1 - l.l_discount) as volume,
         n2.n_name as nation
  from {{schema}}.part p
  join {{schema}}.lineitem l on p.p_partkey = l.l_partkey
  join {{schema}}.supplier s on s.s_suppkey = l.l_suppkey
  join {{schema}}.orders o on o.o_orderkey = l.l_orderkey
  join {{schema}}.customer c on c.c_custkey = o.o_custkey
  join {{schema}}.nation n1 on c.c_nationkey = n1.n_nationkey
  join {{schema}}.region r on n1.n_regionkey = r.r_regionkey
  join {{schema}}.nation n2 on s.s_nationkey = n2.n_nationkey
  where r.r_name = 'AMERICA'
    and o.o_orderdate between date '1995-01-01' and date '1996-12-31'
    and p.p_type = 'ECONOMY ANODIZED STEEL'
) all_nations
group by o_year
order by o_year;
