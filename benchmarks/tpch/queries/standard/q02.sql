select s.s_acctbal, s.s_name, n.n_name, p.p_partkey, p.p_mfgr
from {{schema}}.part p
join {{schema}}.partsupp ps on p.p_partkey = ps.ps_partkey
join {{schema}}.supplier s on s.s_suppkey = ps.ps_suppkey
join {{schema}}.nation n on n.n_nationkey = s.s_nationkey
join {{schema}}.region r on r.r_regionkey = n.n_regionkey
where p.p_size = 15 and p.p_type like '%BRASS' and r.r_name = 'EUROPE'
order by s.s_acctbal desc, n.n_name, s.s_name, p.p_partkey
limit 100;
