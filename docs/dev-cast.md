# The dev cast

Nine local accounts. Six cover every relationship, visibility and
time-zone position the social features have; the eighth (`admin-two`) is
unrelated to that matrix - it exists so admin-console rules that need a
*second* admin (#341: an admin cannot impersonate or disable another admin)
are demonstrable, not just unit-tested. They exist so a demo can *show* a
rule instead of describing it: sign into the seat the rule applies to and
look.

Created by `task seed:dev` (`app/migration/seed_dev.py`). The seeder refuses
to run against anything but the local dev Postgres, so **these credentials
are local-only by construction** - there is no environment they unlock.

## Accounts

Every cast account uses the password **`change-me`**. The target user ("you")
is the seed admin from `env/dev.env` and keeps its own `ADMIN_PASSWORD`.

| handle | email | password | tier | movies | time zone |
| --- | --- | --- | --- | --- | --- |
| `you` | `$ADMIN_EMAIL` | `$ADMIN_PASSWORD` | public | 270 | *unset → deployment `TIME_ZONE`* |
| `friend` | `friend@example.com` | `change-me` | friends | 8 | Europe/London |
| `follower` | `follower@example.com` | `change-me` | public | 2 | Asia/Tokyo |
| `followee` | `followee@example.com` | `change-me` | public | 1 | America/Los_Angeles |
| `public-user` | `public@example.com` | `change-me` | public | 3 | Australia/Sydney |
| `private-user` | `private@example.com` | `change-me` | private | 6 | America/New_York |
| `stranger` | `stranger@example.com` | `change-me` | public | 4 | UTC |
| `admin-two` | `admin-two@gmail.com` | `change-me` | private | 0 | America/Chicago |
| `disposable` | `e2e-disposable@gmail.com` | `change-me` | public | 0 | Pacific/Auckland |

Read `$ADMIN_EMAIL` / `$ADMIN_PASSWORD` out of `env/dev.env` - they are
per-clone, so never hardcode them into a script or a report. `admin-two`'s
own credentials are the literal ones above, no env var - it is a fixed cast
account like the rest, just an admin one.

### What each seat is for

| seat | relationship to `you` | what only this seat can show |
| --- | --- | --- |
| `friend` | accepted friend | friends-only shelves rendering, and `ready` alignment - it shares all eight canon titles |
| `follower` | follows you, not followed back | the asymmetric case: they appear in *your* followers, you are not in theirs |
| `followee` | you follow them | a visible profile with one shelf (books) still `hidden` behind a friends-only tier |
| `public-user` | none | a stranger whose shelves are readable anyway |
| `private-user` | none, private profile | a profile that 404s - with a stocked shelf behind it, so the 404 is the tier and not emptiness |
| `stranger` | none | `not_enough_overlap`: a real shelf, still under the five shared titles alignment needs |
| `disposable` | none | the seat destructive admin tests act on: disable, expire, impersonate. Holds nothing and is re-enabled by every `task seed:dev`, so a test may leave it in any state |
| `admin-two` | none - both are `user_group='admin'` | the only seat that can demo an admin acting *on* another admin: sign in as `you` (or `admin-two`), try to disable or impersonate the other, and get refused. With one admin in the seed this rule was provable only by unit test. |

**The movie counts are load-bearing.** Every title a cast member ranks is
also ranked by `you` (the target is seeded with the whole fixture), so shelf
size *is* shared-title count, and five shared titles is the line between
`not_enough_overlap` and `ready`. Growing `stranger` past four turns it into
a second `friend` and the `not_enough_overlap` state stops existing anywhere
in the seed data.

## Agent instructions: demoing with the cast

Bring the stack up with the `druthers-up` skill first. Then, for whatever is
being demoed, pick the seat from the table above rather than driving
everything as `you` - `you` is public, friendly with everyone and holds the
entire catalog, which is exactly the seat where a broken visibility rule
still looks correct.

**Always name the seat and the credentials in the demo write-up**, so the
reviewer can land on the same screen without going hunting:

> Signed in as `follower@example.com` / `change-me` → <http://localhost:3000/u/you>

### Sign in

Browser: <http://localhost:3000/login>, "Other sign-in options", then email
and password. Google sign-in does not apply to cast accounts.

API, when a check is faster than a click:

```bash
TOKEN=$(curl -s -X POST localhost:8000/v1/auth/token \
  -d 'username=friend@example.com&password=change-me' | jq -r .access_token)
curl -s -H "Authorization: Bearer $TOKEN" localhost:8000/v1/users/me/preferences
```

`/v1/auth/token` is rate-limited. Fetch a token **once** and reuse it across
the checks in a demo - a token per call trips a 429 partway through and the
rest of the run looks like a broken endpoint.

### Which seat for which change

- **Visibility, sharing, privacy tiers** - drive it from `private-user` and
  `stranger`, not from `you`. A rule that leaks shows up as content
  appearing where these two should see nothing. Confirm the *negative*:
  `private-user`'s profile must 404 for everyone else while its own six
  ranked movies are visible from its own seat.
- **Compare / alignment** - `friend` for `ready`, `stranger` for
  `not_enough_overlap`, `followee` for a `hidden` shelf under a visible
  profile. Quote the `shared_ranked_count` alongside the state; the number
  is what makes the state checkable.
- **Friends, follows, requests** - `follower` and `followee` are
  deliberately one-directional. Any change to follow UI should be shown from
  both ends, since they are the only seats where the two directions differ.
- **Time zone, greeting, schedule** - see below.
- **Anything list-shaped (pagination, ranking, filters)** - `you` is the
  only seat with enough rows; the cast shelves are single-page on purpose.
- **Admin console rules involving a second admin** (disable, impersonate) -
  the only pair that can demo this is `you` and `admin-two`; every other
  seat is `user_group='user'` and gets the ordinary non-admin 403 instead
  of the admin-on-admin refusal.

### Demoing per-user time zones

The cast spans UTC-7 to UTC+10, so one wall-clock instant reads as a
different hour - often a different *day* - from each seat. To demo the
greeting or the schedule window, sign into two seats far apart and screenshot
both:

1. `follower@example.com` (Asia/Tokyo) and `followee@example.com`
   (America/Los_Angeles) are 16–17 hours apart. At most times of day one is
   greeted "Good morning" while the other gets "Good evening".
2. The header greeting is server-rendered from the account's zone - reload
   after changing it in **Settings → Time zone** rather than expecting it to
   change live.
3. `you` has **no** zone set, which is the case worth showing on its own: it
   falls back to the deployment's `TIME_ZONE` from `env/dev.env`. Change that
   value, restart the API, and the greeting for `you` moves while every cast
   member stays put.
4. On `/tv/schedule`, the day headings mark the reader's own **Today** /
   **Tomorrow**. Episode dates themselves stay in UTC on purpose - an airdate
   is a calendar date the broadcaster published, not an instant, and shifting
   it by zone would move a show to the wrong night.

## Reseeding

```bash
task seed:dev            # additive and idempotent - safe to re-run
task seed:dev -- --wipe  # clear seeded tracker rows, keep the users
```

Re-running never duplicates a user, a friendship, a follow or a tracker row.
`--wipe` leaves the cast accounts and their relationships in place, so the
seats survive a data reset and the credentials above stay valid.
