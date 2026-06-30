select l_returnflag, l_linestatus,
       sum(l_quantity) as sum_qty,
       sum(l_extendedprice) as sum_base_price,
       avg(l_discount) as avg_disc,
       count(*) as count_order
from {{schema}}.lineitem
where l_shipdate <= date '1998-12-01' - interval '90 days'
group by l_returnflag, l_linestatus
order by l_returnflag, l_linestatus;
