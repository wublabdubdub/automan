select c_count, count(*) as custdist
from (
  select c.c_custkey, count(o.o_orderkey) as c_count
  from {{schema}}.customer c
  left join {{schema}}.orders o on c.c_custkey = o.o_custkey
    and o.o_comment not like '%special%requests%'
  group by c.c_custkey
) c_orders
group by c_count
order by custdist desc, c_count desc;
