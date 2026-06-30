select c.c_custkey, c.c_name,
       sum(l.l_extendedprice * (1 - l.l_discount)) as revenue,
       c.c_acctbal, n.n_name
from {{schema}}.customer c
join {{schema}}.orders o on c.c_custkey = o.o_custkey
join {{schema}}.lineitem l on l.l_orderkey = o.o_orderkey
join {{schema}}.nation n on c.c_nationkey = n.n_nationkey
where o.o_orderdate >= date '1993-10-01'
  and o.o_orderdate < date '1994-01-01'
  and l.l_returnflag = 'R'
group by c.c_custkey, c.c_name, c.c_acctbal, n.n_name
order by revenue desc
limit 20;
