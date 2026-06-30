select sum(l_extendedprice) / 7.0 as avg_yearly
from {{schema}}.lineitem l
join {{schema}}.part p on p.p_partkey = l.l_partkey
where p.p_brand = 'Brand#23'
  and p.p_container = 'MED BOX'
  and l.l_quantity < (
    select 0.2 * avg(l2.l_quantity)
    from {{schema}}.lineitem l2
    where l2.l_partkey = p.p_partkey
  );
