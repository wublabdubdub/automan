select vin,
       count(*) as rows,
       max(mileage) - min(mileage) as mileage_delta,
       avg(speed) as avg_speed
from {{schema}}.{{source_table}}
group by vin
order by mileage_delta desc nulls last
limit 100;
