select date_trunc('day', "timeStamp") as day_bucket,
       count(distinct vin) as active_vins,
       count(*) as rows
from {{schema}}.{{source_table}}
group by 1
order by 1;
