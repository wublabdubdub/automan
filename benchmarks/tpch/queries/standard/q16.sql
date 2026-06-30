select p_brand, p_type, p_size, count(distinct ps_suppkey) as supplier_cnt
from {{schema}}.partsupp ps
join {{schema}}.part p on p.p_partkey = ps.ps_partkey
where p_brand <> 'Brand#45'
  and p_type not like 'MEDIUM POLISHED%'
  and p_size in (49, 14, 23, 45, 19, 3, 36, 9)
group by p_brand, p_type, p_size
order by supplier_cnt desc, p_brand, p_type, p_size;
