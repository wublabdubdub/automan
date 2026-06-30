select s.s_name, s.s_address
from {{schema}}.supplier s
join {{schema}}.nation n on s.s_nationkey = n.n_nationkey
where n.n_name = 'CANADA'
  and exists (
    select 1
    from {{schema}}.partsupp ps
    join {{schema}}.part p on p.p_partkey = ps.ps_partkey
    where ps.ps_suppkey = s.s_suppkey
      and p.p_name like 'forest%'
      and ps.ps_availqty > (
        select 0.5 * sum(l.l_quantity)
        from {{schema}}.lineitem l
        where l.l_partkey = ps.ps_partkey
          and l.l_suppkey = ps.ps_suppkey
          and l.l_shipdate >= date '1994-01-01'
          and l.l_shipdate < date '1995-01-01'
      )
  )
order by s.s_name;
