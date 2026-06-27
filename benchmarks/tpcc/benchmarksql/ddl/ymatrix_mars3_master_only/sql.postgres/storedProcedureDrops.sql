drop function if exists bmsql_proc_new_order(integer, integer, integer, integer[], integer[], integer[]);
drop function if exists bmsql_proc_stock_level(integer, integer, integer);
drop function if exists bmsql_proc_payment(integer, integer, integer, integer, integer, character varying, numeric);
drop function if exists bmsql_proc_order_status(integer, integer, integer, character varying);
drop function if exists bmsql_cid_from_clast(integer, integer, character varying);
drop function if exists bmsql_proc_delivery_bg(integer, integer, timestamp without time zone);
