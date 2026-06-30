select width_bucket(speed, 0, 220, 20) as speed_bucket,
       count(*) as rows,
       min(speed) as min_speed,
       max(speed) as max_speed
from {{schema}}.{{source_table}}
where speed is not null
group by 1
order by 1;
