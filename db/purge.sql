-- Retention: delete events older than 180 days.
-- (Retention period to be confirmed by Shello; see README.)
delete from events where created_at < now() - interval '180 days';
