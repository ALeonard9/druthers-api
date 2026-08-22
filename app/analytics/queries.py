"""
Reproducible queries for product metrics.
These queries assume a schema matching the product_events table:
  id UUID, user_id UUID, event_type VARCHAR, payload JSONB, occurred_at TIMESTAMP
"""

ACTIVATION_QUERY = """
WITH signups AS (
    SELECT user_id, date_trunc('week', occurred_at) AS signup_week
    FROM product_events
    WHERE event_type = 'signup_completed'
),
user_events AS (
    SELECT user_id,
           COUNT(*) FILTER (WHERE event_type = 'fifth_item_ranked') AS rank_events,
           COUNT(*) FILTER (WHERE event_type IN ('first_share', 'profile_completed')) AS activate_events
    FROM product_events
    GROUP BY user_id
)
SELECT s.signup_week,
       COUNT(s.user_id) AS total_signups,
       COUNT(s.user_id) FILTER (WHERE u.rank_events > 0 AND u.activate_events > 0) AS activated_users
FROM signups s
LEFT JOIN user_events u ON s.user_id = u.user_id
GROUP BY s.signup_week
ORDER BY s.signup_week;
"""

SIGNUP_TO_ACTIVATION_CONVERSION_QUERY = """
-- Matches ACTIVATION_QUERY but exposes the ratio explicitly.
WITH signups AS (
    SELECT user_id, date_trunc('week', occurred_at) AS signup_week
    FROM product_events
    WHERE event_type = 'signup_completed'
),
activations AS (
    SELECT user_id
    FROM product_events
    WHERE event_type IN ('fifth_item_ranked', 'first_share', 'profile_completed')
    GROUP BY user_id
    HAVING COUNT(*) FILTER (WHERE event_type = 'fifth_item_ranked') > 0
       AND COUNT(*) FILTER (WHERE event_type IN ('first_share', 'profile_completed')) > 0
)
SELECT
    s.signup_week,
    COUNT(s.user_id) AS total_signups,
    COUNT(a.user_id) AS total_activations,
    CASE
        WHEN COUNT(s.user_id) = 0 THEN 0.0
        ELSE COUNT(a.user_id)::NUMERIC / COUNT(s.user_id)
    END AS conversion_rate
FROM signups s
LEFT JOIN activations a ON s.user_id = a.user_id
GROUP BY s.signup_week
ORDER BY s.signup_week;
"""

WEEKLY_ACTIVATION_QUERY = """
WITH signups AS (
    SELECT user_id, date_trunc('week', occurred_at) AS signup_week, occurred_at AS signup_time
    FROM product_events
    WHERE event_type = 'signup_completed'
),
activations AS (
    SELECT user_id, MAX(occurred_at) AS activation_time
    FROM product_events
    WHERE event_type IN ('fifth_item_ranked', 'first_share', 'profile_completed')
    GROUP BY user_id
    HAVING COUNT(*) FILTER (WHERE event_type = 'fifth_item_ranked') > 0
       AND COUNT(*) FILTER (WHERE event_type IN ('first_share', 'profile_completed')) > 0
)
SELECT
    s.signup_week,
    COUNT(s.user_id) AS signups,
    COUNT(a.user_id) AS activated_within_7d
FROM signups s
LEFT JOIN activations a ON s.user_id = a.user_id
    AND a.activation_time <= s.signup_time + INTERVAL '7 days'
GROUP BY s.signup_week
ORDER BY s.signup_week;
"""

D7_RETENTION_QUERY = """
WITH signups AS (
    SELECT user_id, date_trunc('week', occurred_at) AS signup_week, occurred_at::date AS signup_date
    FROM product_events
    WHERE event_type = 'signup_completed'
),
returns AS (
    SELECT DISTINCT user_id, occurred_at::date AS return_date
    FROM product_events
    WHERE event_type = 'returning_session'
)
SELECT
    s.signup_week,
    COUNT(DISTINCT s.user_id) AS cohort_size,
    COUNT(DISTINCT r.user_id) AS d7_retained
FROM signups s
LEFT JOIN returns r ON s.user_id = r.user_id
    AND r.return_date >= s.signup_date + 7
    AND r.return_date <= s.signup_date + 13
GROUP BY s.signup_week
ORDER BY s.signup_week;
"""

D28_RETENTION_QUERY = """
WITH signups AS (
    SELECT user_id, date_trunc('week', occurred_at) AS signup_week, occurred_at::date AS signup_date
    FROM product_events
    WHERE event_type = 'signup_completed'
),
returns AS (
    SELECT DISTINCT user_id, occurred_at::date AS return_date
    FROM product_events
    WHERE event_type = 'returning_session'
)
SELECT
    s.signup_week,
    COUNT(DISTINCT s.user_id) AS cohort_size,
    COUNT(DISTINCT r.user_id) AS d28_retained
FROM signups s
LEFT JOIN returns r ON s.user_id = r.user_id
    AND r.return_date >= s.signup_date + 28
    AND r.return_date <= s.signup_date + 34
GROUP BY s.signup_week
ORDER BY s.signup_week;
"""

SHARE_TO_SIGNUP_CONVERSION_QUERY = """
WITH shares AS (
    SELECT date_trunc('week', occurred_at) AS cohort_week, COUNT(*) AS total_shares
    FROM product_events
    WHERE event_type = 'first_share'
    GROUP BY 1
),
invite_signups AS (
    SELECT date_trunc('week', occurred_at) AS cohort_week, COUNT(DISTINCT user_id) AS total_invite_signups
    FROM product_events
    WHERE event_type = 'signup_completed'
    AND payload->>'source' = 'invite'
    GROUP BY 1
)
SELECT
    COALESCE(s.cohort_week, i.cohort_week) AS week,
    COALESCE(s.total_shares, 0) AS total_shares,
    COALESCE(i.total_invite_signups, 0) AS total_invite_signups,
    CASE
        WHEN COALESCE(s.total_shares, 0) = 0 THEN 0.0
        ELSE COALESCE(i.total_invite_signups, 0)::NUMERIC / s.total_shares
    END AS share_conversion_rate
FROM shares s
FULL OUTER JOIN invite_signups i ON s.cohort_week = i.cohort_week
ORDER BY week;
"""

WEEKLY_EVENT_COUNTS_QUERY = """
-- Covers all other product events to ensure nothing is unmeasured (e.g. onboarding_started, invite_opened)
SELECT
    date_trunc('week', occurred_at) AS week,
    event_type,
    COUNT(*) AS event_count,
    COUNT(DISTINCT user_id) AS unique_users
FROM product_events
GROUP BY 1, 2
ORDER BY 1, 2;
"""
