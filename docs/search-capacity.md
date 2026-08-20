# Search capacity

One Cloud Run instance admits 80 concurrent HTTP requests. The application
declares that deployment value, sets AnyIO's synchronous worker limit to 40,
and sets SQLAlchemy's pool to 10 persistent plus 10 overflow connections in
`app/config.py`.

Those numbers describe three different phases, so they do not need to be
equal. An instance can accept 80 requests and run 40 blocking search jobs on
the bounded catalog-search executor while serving short authentication and
tracked-status queries from 20 database connections. A single-domain job
makes one provider call; a global search job fans out to four provider threads.
Catalog search handlers close their session before calling TMDB, TVMaze, Open
Library, or IGDB, including the spelling-correction retry, then reacquire a
connection only to decorate returned hits. The other 40 admitted requests can
wait for a search worker without also waiting on or occupying Postgres.

Cloud Run's `containerConcurrency` remains deployment configuration owned by
`druthers-infra`. Keep it at 80 unless the sync worker limit and this capacity
model are changed with it. Every catalog search request has a 20-second total
handler deadline, far below Cloud Run's 300-second request timeout.
