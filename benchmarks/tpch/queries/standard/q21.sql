select s.s_name, count(*) as numwait
from {{schema}}.supplier s
join {{schema}}.lineitem l1 on s.s_suppkey = l1.l_suppkey
join {{schema}}.orders o on o.o_orderkey = l1.l_orderkey
join {{schema}}.nation n on s.s_nationkey = n.n_nationkey
where o.o_orderstatus = 'F'
  and l1.l_receiptdate > l1.l_commitdate
  and exists (
    select 1 from {{schema}}.lineitem l2
    where l2.l_orderkey = l1.l_orderkey
      and l2.l_suppkey <> l1.l_suppkey
  )
  and not exists (
    select 1 from {{schema}}.lineitem l3
    where l3.l_orderkey = l1.l_orderkey
      and l3.l_suppkey <> l1.l_suppkey
      and l3.l_receiptdate > l3.l_commitdate
  )
  and n.n_name = 'SAUDI ARABIA'
group by s.s_name
order by numwait desc, s.s_name
limit 100;
