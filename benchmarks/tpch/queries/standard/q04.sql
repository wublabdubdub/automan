select o_orderpriority, count(*) as order_count
from {{schema}}.orders o
where o_orderdate >= date '1993-07-01'
  and o_orderdate < date '1993-07-01' + interval '3 months'
  and exists (
    select 1 from {{schema}}.lineitem l
    where l.l_orderkey = o.o_orderkey
      and l.l_commitdate < l.l_receiptdate
  )
group by o_orderpriority
order by o_orderpriority;
