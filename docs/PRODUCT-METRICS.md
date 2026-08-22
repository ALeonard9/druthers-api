# Product Metrics and Analytics Strategy

## 1. Analytics Approach

Druthers relies on a **privacy-first, self-hosted** analytics approach using our existing PostgreSQL database (Neon).

*   **Cost:** $0 marginal cost. Piggybacks on our existing database compute and storage. Fits well within the $10/month hobby scale budget.
*   **Retention:** Indefinite by default, but subject to aggressive data-minimization at capture (see payloads).
*   **Deletion:** Event data is bound to a `user_id`. When an account is deleted, a cascading delete or a targeted scrub query (defined below) removes all associated analytics events, complying with our privacy promises.
*   **Vendor Lock-In:** None. Standard SQL queries compute all metrics.

## 2. Event Payloads and Privacy Contract

To prevent leaking PII, **all event payloads must strictly exclude:**
*   Emails
*   User handles
*   Search terms
*   Private notes
*   Title names (movies, books, etc.)

Events are recorded in a `product_events` table:
```sql
CREATE TABLE product_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    occurred_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    payload JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX idx_product_events_user_id ON product_events(user_id);
CREATE INDEX idx_product_events_type_time ON product_events(event_type, occurred_at);
```

Defined event types:
1.  `signup_completed`
2.  `onboarding_started`
3.  `first_item_added`
4.  `fifth_item_ranked`
5.  `first_share`
6.  `invite_opened` (payload includes: `source=share_link`)
7.  `friendship_established`
8.  `comparison_viewed` (payload includes: `domain`)
9.  `returning_session` (returning session marker)
10. `profile_completed`

## 3. Definitions

*   **Activation:** A user is considered *activated* if they have ranked at least 5 items AND (completed their profile OR shared a list). We track activation when `fifth_item_ranked` is present alongside a `first_share` or `profile_completed` event.
*   **Weekly Activation:** Percentage of signups in a given week that reach Activation within 7 days.
*   **D7 Retention:** Percentage of users who have a `returning_session` event on days 7-13 after `signup_completed`.
*   **D28 Retention:** Percentage of users who have a `returning_session` event on days 28-34 after `signup_completed`.
*   **Share-to-Signup Conversion:** Number of `signup_completed` events resulting from an invite vs. total `first_share` events.
*   **Signup-to-Activation Conversion:** Total activated users / Total signups.

## 4. Data Deletion

When a user deletes their account, the raw event data is removed via:
```sql
DELETE FROM product_events WHERE user_id = $1;
```
This is executed as part of the standard user deletion transaction.

To prevent deletion from silently rewriting historical metrics (e.g., a cohort size shrinking months later), the system maintains a periodic rollup table holding aggregated metrics per cohort period (e.g., weekly totals). This rollup table contains no `user_id` or PII and is preserved after account deletion, keeping trend lines accurate while scrubbing personal data.
