select sum(l.l_extendedprice * (1 - l.l_discount)) as revenue
from {{schema}}.lineitem l
join {{schema}}.part p on p.p_partkey = l.l_partkey
where (
    p.p_brand = 'Brand#12'
    and p.p_container in ('SM CASE', 'SM BOX', 'SM PACK', 'SM PKG')
    and l.l_quantity between 1 and 11
    and p.p_size between 1 and 5
    and l.l_shipmode in ('AIR', 'AIR REG')
    and l.l_shipinstruct = 'DELIVER IN PERSON'
  )
  or (
    p.p_brand = 'Brand#23'
    and p.p_container in ('MED BAG', 'MED BOX', 'MED PKG', 'MED PACK')
    and l.l_quantity between 10 and 20
    and p.p_size between 1 and 10
    and l.l_shipmode in ('AIR', 'AIR REG')
    and l.l_shipinstruct = 'DELIVER IN PERSON'
  );
