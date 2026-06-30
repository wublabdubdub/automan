select l.l_orderkey,
       sum(l.l_extendedprice * (1 - l.l_discount)) as revenue,
       o.o_orderdate,
       o.o_shippriority
from {{schema}}.customer c
join {{schema}}.orders o on c.c_custkey = o.o_custkey
join {{schema}}.lineitem l on l.l_orderkey = o.o_orderkey
where c.c_mktsegment = 'BUILDING'
  and o.o_orderdate < date '1995-03-15'
  and l.l_shipdate > date '1995-03-15'
group by l.l_orderkey, o.o_orderdate, o.o_shippriority
order by revenue desc, o.o_orderdate
limit 10;
