select date_trunc('hour', "timeStamp") as hour_bucket,
       count(*) as rows,
       avg(speed) as avg_speed,
       max(speed) as max_speed
from {{schema}}.{{source_table}}
group by 1
order by 1
limit 200;
