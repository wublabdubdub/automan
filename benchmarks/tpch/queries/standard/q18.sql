select c.c_name, c.c_custkey, o.o_orderkey, o.o_orderdate, o.o_totalprice,
       sum(l.l_quantity) as quantity
from {{schema}}.customer c
join {{schema}}.orders o on c.c_custkey = o.o_custkey
join {{schema}}.lineitem l on o.o_orderkey = l.l_orderkey
where o.o_orderkey in (
  select l_orderkey
  from {{schema}}.lineitem
  group by l_orderkey
  having sum(l_quantity) > 300
)
group by c.c_name, c.c_custkey, o.o_orderkey, o.o_orderdate, o.o_totalprice
order by o.o_totalprice desc, o.o_orderdate
limit 100;
