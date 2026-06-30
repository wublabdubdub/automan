with bounds as (
  select max("timeStamp") as max_ts
  from {{schema}}.{{source_table}}
)
select count(*) as rows,
       count(distinct vin) as vins,
       avg(speed) as avg_speed,
       avg(mileage) as avg_mileage
from {{schema}}.{{source_table}} t
cross join bounds b
where t."timeStamp" >= b.max_ts - interval '1 day';
